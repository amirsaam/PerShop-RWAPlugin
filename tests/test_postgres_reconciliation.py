"""Operator-visible, evidence-based PerShop reconciliation tests."""
from __future__ import annotations

import sys
import types
import uuid

import pytest

from postgres_support import Context
from test_postgres_billing import pg_test
from test_postgres_api_users import (
    ApiPanel, Quota, api_scope, assert_one_complete_user, create, setup_shop, stage,
)

from rwa_plugin_pershop import reconciliation, service


@pg_test
async def test_ownership_reconciliation_retries_only_idempotent_stamping(pool):
    shop = await setup_shop(pool)
    panel, quota = ApiPanel(), Quota()
    key = f"ownership-recovery-{uuid.uuid4().hex}"
    await pool.execute("""
        CREATE FUNCTION fail_recovery_stamp() RETURNS trigger AS $$
        BEGIN RAISE EXCEPTION 'ownership unavailable'; RETURN NEW; END
        $$ LANGUAGE plpgsql
    """)
    await pool.execute("""
        CREATE TRIGGER fail_recovery_stamp BEFORE UPDATE OF created_by_admin_id
        ON users FOR EACH ROW EXECUTE FUNCTION fail_recovery_stamp()
    """)
    journal_id = None
    try:
        async with api_scope(panel, quota):
            with pytest.raises(RuntimeError):
                await create(Context(pool), shop, key)
            journal_id = await pool.fetchval(
                "SELECT id FROM plugin_pershop_api_user_creates WHERE shop_id=$1",
                shop["id"])
            incidents = await reconciliation.list_incidents(Context(pool), shop)
            assert len(incidents["creations"]) == 1
            assert incidents["creations"][0]["reason"] == "ownership_stamping"
            assert "idempotency_key" not in str(incidents)
            with pytest.raises(ValueError):
                await reconciliation.resume_ownership(
                    Context(pool), dict(shop, id=shop["id"] + 1), journal_id, "owner")
    finally:
        await pool.execute("DROP TRIGGER fail_recovery_stamp ON users")
        await pool.execute("DROP FUNCTION fail_recovery_stamp()")
    async with api_scope(panel, quota):
        result = await reconciliation.resume_ownership(
            Context(pool), shop, journal_id, "owner")
        assert result["uuid"] == "uuid-1" and result["sub_id"] is not None
        await assert_one_complete_user(pool, shop, panel, quota)
        assert (await reconciliation.list_incidents(Context(pool), shop)) == {
            "creations": [], "updates": []}


@pg_test
async def test_operator_verified_quota_resolution_resumes_without_recharging(pool):
    shop = await setup_shop(pool)
    panel, quota = ApiPanel(), Quota()
    key = f"quota-recovery-{uuid.uuid4().hex}"
    quota.fail_counter = "users_created"
    quota.after_call = True
    async with api_scope(panel, quota):
        with pytest.raises(RuntimeError):
            await create(Context(pool), shop, key)
        journal_id = await pool.fetchval(
            "SELECT id FROM plugin_pershop_api_user_creates WHERE shop_id=$1",
            shop["id"])
        with pytest.raises(ValueError, match="verified outcome required"):
            await reconciliation.resolve_create_quota(
                Context(pool), shop, journal_id, "owner", {})
        assert (await stage(pool, shop, key))["state"] == "reconciliation"
        quota.fail_counter = None
        result = await reconciliation.resolve_create_quota(
            Context(pool), shop, journal_id, "owner",
            {"quota_users": "charged"})
        assert result["uuid"] == "uuid-1"
        # The uncertain users counter is not called again. Only the following,
        # never-attempted traffic counter is charged by normal recovery.
        assert [call[1] for call in quota.calls] == [
            "users_created", "traffic_used_bytes"]
        assert await pool.fetchval("SELECT COUNT(*) FROM users") == 1
        assert await pool.fetchval("SELECT COUNT(*) FROM plugin_pershop_subs") == 1
        completed = await stage(pool, shop, key)
        assert completed["state"] == "completed"
        assert completed["quota_resolution"]["by"] == "owner"
        assert completed["quota_resolution"]["outcomes"] == {"quota_users": "charged"}


class RestoreFailingPanel:
    def __init__(self):
        self.updated = []

    async def update_user(self, user_uuid, **fields):
        self.updated.append((user_uuid, dict(fields)))
        if len(self.updated) == 2:
            raise RuntimeError("compensation failed")
        return {"response": {"ok": True}}


@pg_test
async def test_update_compensation_failure_is_durable_and_manually_resolved(pool):
    shop = await setup_shop(pool)
    user_uuid = "update-user"
    await pool.execute(
        """INSERT INTO users(uuid,username,created_by_admin_id,traffic_limit_bytes)
           VALUES($1,'vip-update',7,$2)""", user_uuid, 1073741824)
    panel, quota = RestoreFailingPanel(), Quota()
    quota.fail_counter = "traffic_used_bytes"
    quota.after_call = True
    saved = {}
    for name in ("web.backend.core.rbac", "shared", "shared.data_access"):
        saved[name] = sys.modules.get(name)
        sys.modules[name] = types.ModuleType(name)

    async def visible(_ns):
        return {user_uuid}

    async def resolve(user_id):
        return user_id

    sys.modules["web.backend.core.rbac"].get_visible_user_uuids = visible
    sys.modules["shared.data_access"].resolve_panel_user_id = resolve
    try:
        async with api_scope(panel, quota):
            with pytest.raises(RuntimeError, match="user update requires reconciliation"):
                await service.api_update_user(
                    Context(pool), shop, user_uuid, traffic_limit_bytes=3 * 1073741824)
            row = await pool.fetchrow(
                "SELECT * FROM plugin_pershop_update_incidents WHERE shop_id=$1",
                shop["id"])
            assert row["user_uuid"] == user_uuid
            assert row["previous_traffic_limit_bytes"] == 1073741824
            assert row["attempted_traffic_limit_bytes"] == 3 * 1073741824
            assert row["quota_delta_bytes"] == 2 * 1073741824
            assert row["reason"] == "uncertain_traffic_quota"
            assert row["compensation"] == "failed" and row["state"] == "reconciliation"
            incidents = await reconciliation.list_incidents(Context(pool), shop)
            assert len(incidents["updates"]) == 1
            with pytest.raises(ValueError):
                await reconciliation.resolve_update_incident(
                    Context(pool), dict(shop, id=shop["id"] + 1), row["id"], "owner", "foreign")
            closed = await reconciliation.resolve_update_incident(
                Context(pool), shop, row["id"], "owner",
                "Verified panel and traffic counter manually")
            assert closed["state"] == "resolved"
            assert closed["resolution_note"] == "Verified panel and traffic counter manually"
            assert (await reconciliation.list_incidents(Context(pool), shop))["updates"] == []
            assert len(panel.updated) == 2
    finally:
        for name, previous in saved.items():
            if previous is None:
                sys.modules.pop(name, None)
            else:
                sys.modules[name] = previous
