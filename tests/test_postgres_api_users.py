"""Real PostgreSQL recovery and concurrency checks for POST /pub/users."""
from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
import json
import sys
import uuid

import pytest

from postgres_support import Context, seed_shop
from test_postgres_billing import _core_users, pg_test
from rwa_plugin_pershop import provisioning, service

EXP = datetime.now(timezone.utc) + timedelta(days=30)
GB = 1073741824


class ApiPanel:
    def __init__(self):
        self.created = []

    async def create_user(self, **kwargs):
        self.created.append(dict(kwargs))
        number = len(self.created)
        return {"response": {
            "id": 1000 + number, "uuid": f"uuid-{number}",
            "username": kwargs["username"], "subscriptionUrl": "https://sub/x",
            "trafficLimitBytes": kwargs.get("traffic_limit_bytes"),
            "expireAt": datetime.fromisoformat(kwargs["expire_at"]),
            "telegramId": kwargs.get("telegram_id"),
            "description": kwargs.get("description"),
        }}


class Quota:
    def __init__(self):
        self.calls = []
        self.fail_counter = None
        self.after_call = False

    async def __call__(self, owner_id, counter, *args):
        self.calls.append((owner_id, counter, *args))
        if self.fail_counter == counter and not self.after_call:
            raise RuntimeError("injected quota transport failure")
        if self.fail_counter == counter and self.after_call:
            self.fail_counter = None
            raise RuntimeError("quota response lost")
        return True


@asynccontextmanager
async def api_scope(panel, quota):
    old_row = service._owner_account_row
    old_policy = service._owner_policy
    old_client = service._panel_client
    module = sys.modules["web.backend.core.admin_accounts"]
    old_counter = module.increment_usage_counter

    async def account_row(_ctx, _owner):
        return True, {"id": 7, "role_id": 1, "role_name": "admin"}

    async def policy(_owner):
        return "admin", "allowed"

    service._owner_account_row = account_row
    service._owner_policy = policy
    service._panel_client = lambda: panel
    module.increment_usage_counter = quota
    try:
        yield
    finally:
        service._owner_account_row = old_row
        service._owner_policy = old_policy
        service._panel_client = old_client
        module.increment_usage_counter = old_counter


async def setup_shop(pool):
    await _core_users(pool)
    shop_id, _package = await seed_shop(pool, credit="0")
    shop = dict(await pool.fetchrow(
        "SELECT * FROM plugin_pershop_shops WHERE id=$1", shop_id))
    return shop


async def create(ctx, shop, key, **changes):
    kwargs = dict(username=f"vip-{key}", expire_at=EXP,
                  traffic_limit_bytes=GB, telegram_id=555,
                  idempotency_key=key)
    kwargs.update(changes)
    return await service.api_create_user(ctx, shop, **kwargs)


async def stage(pool, shop, key):
    raw = await pool.fetchval(
        """SELECT stage FROM plugin_pershop_api_user_creates
           WHERE shop_id=$1 AND idempotency_key=$2""", shop["id"], key)
    return json.loads(raw)


async def assert_one_complete_user(pool, shop, panel, quota, expected_uuid="uuid-1"):
    assert len(panel.created) == 1
    assert await pool.fetchval("SELECT COUNT(*) FROM users") == 1
    assert await pool.fetchval("SELECT COUNT(*) FROM plugin_pershop_subs") == 1
    assert await pool.fetchval(
        "SELECT created_by_admin_id FROM users WHERE uuid=$1", expected_uuid) == 7
    assert [call[1] for call in quota.calls] == ["users_created", "traffic_used_bytes"]


@pg_test
async def test_overlapping_retries_create_stamp_charge_and_save_once(pool):
    shop = await setup_shop(pool)
    panel, quota = ApiPanel(), Quota()
    key = f"race-{uuid.uuid4().hex}"
    async with api_scope(panel, quota):
        results = await asyncio.gather(*[
            create(Context(pool), shop, key) for _ in range(12)
        ], return_exceptions=True)
        assert sum(isinstance(result, dict) for result in results) == 1, results
        replayed = await asyncio.gather(*[
            create(Context(pool), shop, key) for _ in range(6)
        ])
        assert all(result == replayed[0] for result in replayed)
        with pytest.raises(ValueError, match="another request"):
            await create(Context(pool), shop, key, telegram_id=556)
        await assert_one_complete_user(pool, shop, panel, quota)
        assert (await stage(pool, shop, key))["state"] == "completed"


@pg_test
async def test_lost_panel_response_adopts_exact_account_without_new_create(pool):
    shop = await setup_shop(pool)
    panel, quota = ApiPanel(), Quota()
    key = f"lost-{uuid.uuid4().hex}"
    original = provisioning._panel_create

    async def create_mirror_and_lose(ctx, request):
        result = await original(ctx, request)
        await provisioning._materialize(ctx, {}, request, result)
        raise RuntimeError("panel create response lost")

    provisioning._panel_create = create_mirror_and_lose
    try:
        async with api_scope(panel, quota):
            with pytest.raises(RuntimeError, match="response lost"):
                await create(Context(pool), shop, key)
            assert (await stage(pool, shop, key))["state"] == "creating"
            assert await pool.fetchval("SELECT COUNT(*) FROM plugin_pershop_subs") == 0
    finally:
        provisioning._panel_create = original
    async with api_scope(panel, quota):
        result = await create(Context(pool), shop, key)
        assert result["uuid"] == "uuid-1"
        await assert_one_complete_user(pool, shop, panel, quota)


@pg_test
async def test_unknown_absence_before_create_never_guesses_a_second_account(pool):
    shop = await setup_shop(pool)
    panel, quota = ApiPanel(), Quota()
    key = f"absent-{uuid.uuid4().hex}"
    original = provisioning._panel_create

    async def fail_before_remote_call(_ctx, _request):
        raise RuntimeError("connection failed before create")

    provisioning._panel_create = fail_before_remote_call
    try:
        async with api_scope(panel, quota):
            with pytest.raises(RuntimeError, match="before create"):
                await create(Context(pool), shop, key)
    finally:
        provisioning._panel_create = original
    async with api_scope(panel, quota):
        with pytest.raises(RuntimeError):
            await create(Context(pool), shop, key)
        assert panel.created == []
        assert await pool.fetchval("SELECT COUNT(*) FROM users") == 0
        assert quota.calls == []


@pg_test
async def test_created_result_save_fault_retries_by_exact_adoption(pool):
    shop = await setup_shop(pool)
    panel, quota = ApiPanel(), Quota()
    key = f"save-{uuid.uuid4().hex}"
    await pool.execute("""
        CREATE FUNCTION fail_api_created_save() RETURNS trigger AS $$
        BEGIN
            IF NEW.stage::jsonb->>'state' = 'created' THEN
                RAISE EXCEPTION 'injected created-result save fault';
            END IF;
            RETURN NEW;
        END
        $$ LANGUAGE plpgsql
    """)
    await pool.execute("""
        CREATE TRIGGER fail_api_created_save BEFORE UPDATE OF stage
        ON plugin_pershop_api_user_creates FOR EACH ROW
        EXECUTE FUNCTION fail_api_created_save()
    """)
    try:
        async with api_scope(panel, quota):
            with pytest.raises(RuntimeError, match="requires reconciliation") as raised:
                await create(Context(pool), shop, key)
            assert "created-result save fault" in str(raised.value.__cause__)
            assert len(panel.created) == 1
            assert await pool.fetchval("SELECT COUNT(*) FROM users") == 1
            assert quota.calls == []
    finally:
        await pool.execute(
            "DROP TRIGGER fail_api_created_save ON plugin_pershop_api_user_creates")
        await pool.execute("DROP FUNCTION fail_api_created_save()")
    async with api_scope(panel, quota):
        await create(Context(pool), shop, key)
        await assert_one_complete_user(pool, shop, panel, quota)


@pg_test
async def test_ownership_stamp_fault_fails_closed_without_quota_or_success(pool):
    shop = await setup_shop(pool)
    panel, quota = ApiPanel(), Quota()
    key = f"stamp-{uuid.uuid4().hex}"
    await pool.execute("""
        CREATE FUNCTION fail_api_stamp() RETURNS trigger AS $$
        BEGIN RAISE EXCEPTION 'injected ownership fault'; RETURN NEW; END
        $$ LANGUAGE plpgsql
    """)
    await pool.execute("""
        CREATE TRIGGER fail_api_stamp BEFORE UPDATE OF created_by_admin_id
        ON users FOR EACH ROW EXECUTE FUNCTION fail_api_stamp()
    """)
    try:
        async with api_scope(panel, quota):
            with pytest.raises(RuntimeError, match="ownership requires reconciliation") as raised:
                await create(Context(pool), shop, key)
            assert "ownership fault" in str(raised.value.__cause__)
            assert await pool.fetchval(
                "SELECT created_by_admin_id FROM users WHERE username=$1",
                f"vip-{key}") is None
            assert await pool.fetchval("SELECT COUNT(*) FROM plugin_pershop_subs") == 0
            assert quota.calls == []
            assert (await stage(pool, shop, key))["state"] == "reconciliation"
    finally:
        await pool.execute("DROP TRIGGER fail_api_stamp ON users")
        await pool.execute("DROP FUNCTION fail_api_stamp()")
    async with api_scope(panel, quota):
        with pytest.raises(RuntimeError):
            await create(Context(pool), shop, key)
        assert len(panel.created) == 1
        assert quota.calls == []


@pg_test
async def test_stamped_state_save_fault_cannot_report_success(pool):
    shop = await setup_shop(pool)
    panel, quota = ApiPanel(), Quota()
    key = f"stamp-save-{uuid.uuid4().hex}"
    await pool.execute("""
        CREATE FUNCTION fail_stamped_stage() RETURNS trigger AS $$
        BEGIN
            IF NEW.stage::jsonb->>'state' = 'stamped' THEN
                RAISE EXCEPTION 'injected stamped-state save fault';
            END IF;
            RETURN NEW;
        END $$ LANGUAGE plpgsql
    """)
    await pool.execute("""
        CREATE TRIGGER fail_stamped_stage BEFORE UPDATE OF stage
        ON plugin_pershop_api_user_creates FOR EACH ROW
        EXECUTE FUNCTION fail_stamped_stage()
    """)
    try:
        async with api_scope(panel, quota):
            with pytest.raises(RuntimeError, match="ownership requires reconciliation") as raised:
                await create(Context(pool), shop, key)
            assert "stamped-state save fault" in str(raised.value.__cause__)
            assert await pool.fetchval("SELECT created_by_admin_id FROM users") == 7
            assert await pool.fetchval("SELECT COUNT(*) FROM plugin_pershop_subs") == 0
            assert quota.calls == []
    finally:
        await pool.execute(
            "DROP TRIGGER fail_stamped_stage ON plugin_pershop_api_user_creates")
        await pool.execute("DROP FUNCTION fail_stamped_stage()")
    async with api_scope(panel, quota):
        with pytest.raises(RuntimeError):
            await create(Context(pool), shop, key)
        assert len(panel.created) == 1 and quota.calls == []


@pg_test
async def test_uncertain_quota_outcome_never_repeats_counter(pool):
    shop = await setup_shop(pool)
    panel, quota = ApiPanel(), Quota()
    key = f"quota-{uuid.uuid4().hex}"
    quota.fail_counter = "users_created"
    quota.after_call = True
    async with api_scope(panel, quota):
        with pytest.raises(RuntimeError, match="quota requires reconciliation") as raised:
            await create(Context(pool), shop, key)
        assert "quota response lost" in str(raised.value.__cause__)
        assert (await stage(pool, shop, key))["state"] == "reconciliation"
        quota.fail_counter = None
        with pytest.raises(RuntimeError):
            await create(Context(pool), shop, key)
        assert len(panel.created) == 1
        assert [call[1] for call in quota.calls] == ["users_created"]
        assert await pool.fetchval("SELECT COUNT(*) FROM plugin_pershop_subs") == 0


@pg_test
async def test_subscription_insert_fault_resumes_without_new_create_or_charge(pool):
    shop = await setup_shop(pool)
    panel, quota = ApiPanel(), Quota()
    key = f"sub-save-{uuid.uuid4().hex}"
    await pool.execute("""
        CREATE FUNCTION fail_api_sub_insert() RETURNS trigger AS $$
        BEGIN RAISE EXCEPTION 'injected subscription save fault'; RETURN NEW; END
        $$ LANGUAGE plpgsql
    """)
    await pool.execute("""
        CREATE TRIGGER fail_api_sub_insert BEFORE INSERT ON plugin_pershop_subs
        FOR EACH ROW EXECUTE FUNCTION fail_api_sub_insert()
    """)
    try:
        async with api_scope(panel, quota):
            with pytest.raises(RuntimeError, match="finalization requires reconciliation") as raised:
                await create(Context(pool), shop, key)
            assert "subscription save fault" in str(raised.value.__cause__)
            assert (await stage(pool, shop, key))["state"] == "ready"
            assert [call[1] for call in quota.calls] == [
                "users_created", "traffic_used_bytes"]
            assert await pool.fetchval("SELECT COUNT(*) FROM plugin_pershop_subs") == 0
    finally:
        await pool.execute(
            "DROP TRIGGER fail_api_sub_insert ON plugin_pershop_subs")
        await pool.execute("DROP FUNCTION fail_api_sub_insert()")
    async with api_scope(panel, quota):
        await create(Context(pool), shop, key)
        await assert_one_complete_user(pool, shop, panel, quota)


@pg_test
async def test_completed_response_can_be_lost_and_replayed_without_side_effects(pool):
    shop = await setup_shop(pool)
    panel, quota = ApiPanel(), Quota()
    key = f"complete-{uuid.uuid4().hex}"
    async with api_scope(panel, quota):
        original_service = service.api_create_user

        async def create_save_then_lose_http_response(*args, **kwargs):
            await original_service(*args, **kwargs)
            raise RuntimeError("HTTP response lost after completed-response save")

        service.api_create_user = create_save_then_lose_http_response
        try:
            with pytest.raises(RuntimeError, match="HTTP response lost"):
                await create(Context(pool), shop, key)
        finally:
            service.api_create_user = original_service
        assert (await stage(pool, shop, key))["state"] == "completed"
        real_complete = provisioning._complete

        async def never_called(*_args):
            raise AssertionError("a completed response must not be provisioned again")

        provisioning._complete = never_called
        try:
            result = await create(Context(pool), shop, key)
            assert result["uuid"] == "uuid-1" and result["sub_id"] is not None
        finally:
            provisioning._complete = real_complete
        await assert_one_complete_user(pool, shop, panel, quota)


async def _collision_case(pool, incomplete):
    shop = await setup_shop(pool)
    panel, quota = ApiPanel(), Quota()
    key = f"collision-{uuid.uuid4().hex}"
    description = f"pershop:vip api:{shop['id']}:{key}"
    await pool.execute(
        """INSERT INTO users(uuid,username,description,telegram_id)
           VALUES($1,$2,$3,$4)""",
        "" if incomplete else "foreign-uuid", f"vip-{key}",
        description if incomplete else "foreign owner", None if incomplete else 999)
    async with api_scope(panel, quota):
        with pytest.raises(RuntimeError):
            await create(Context(pool), shop, key)
        assert panel.created == [] and quota.calls == []
        assert await pool.fetchval("SELECT COUNT(*) FROM users") == 1
        assert await pool.fetchval("SELECT COUNT(*) FROM plugin_pershop_subs") == 0


@pg_test
async def test_foreign_exact_username_collision_fails_closed(pool):
    await _collision_case(pool, False)


@pg_test
async def test_incomplete_exact_username_collision_fails_closed(pool):
    await _collision_case(pool, True)
