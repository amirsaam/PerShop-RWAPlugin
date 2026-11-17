"""Trial provisioning recovery tests against real migrated PostgreSQL."""
from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
import json
from types import SimpleNamespace

import pytest

from postgres_support import Context, seed_shop
from test_postgres_billing import ApprovalPanel, _core_users, pg_test


def _context(pool):
    ctx = Context(pool)

    async def get_setting(key, default=None):
        return default

    ctx.settings = SimpleNamespace(get=get_setting)
    return ctx


async def _trial_shop(pool):
    shop_id, _ = await seed_shop(pool, credit="0")
    await pool.execute(
        "UPDATE plugin_pershop_shops SET test_enabled=TRUE WHERE id=$1", shop_id)
    return dict(await pool.fetchrow(
        "SELECT * FROM plugin_pershop_shops WHERE id=$1", shop_id))


@pg_test
async def test_parallel_trials_allocate_unique_names_and_one_claim_per_buyer(pool):
    from rwa_plugin_pershop import service

    await _core_users(pool)
    shop = await _trial_shop(pool)
    panel = ApprovalPanel(pool)
    old_client = service._panel_client
    service._panel_client = lambda: panel
    try:
        results = await asyncio.gather(*[
            service.claim_test_sub(_context(pool), shop, 1000 + index, "buyer")
            for index in range(10)
        ], return_exceptions=True)
        assert all(isinstance(result, dict) for result in results), results
        same_buyer = await asyncio.gather(*[
            service.claim_test_sub(_context(pool), shop, 2222, "buyer")
            for _ in range(12)
        ], return_exceptions=True)
    finally:
        service._panel_client = old_client
    assert sum(isinstance(result, dict) for result in same_buyer) == 1, same_buyer
    assert all(isinstance(result, (dict, ValueError)) for result in same_buyer)
    assert len(panel.created) == 11
    assert len({call["username"].casefold() for call in panel.created}) == 11
    assert await pool.fetchval(
        "SELECT test_counter FROM plugin_pershop_shops WHERE id=$1", shop["id"]) == 11
    assert await pool.fetchval("SELECT COUNT(*) FROM plugin_pershop_test_claims") == 11
    assert await pool.fetchval("SELECT COUNT(*) FROM plugin_pershop_subs") == 11
    assert await pool.fetchval(
        "SELECT COUNT(*) FROM plugin_pershop_test_claims "
        "WHERE provision_stage::jsonb->>'finalized'='true'") == 11


@pg_test
async def test_trial_adopts_lost_create_without_repeating_or_renaming(pool):
    from rwa_plugin_pershop import service

    await _core_users(pool)
    shop = await _trial_shop(pool)
    panel = ApprovalPanel(pool)
    old_client, old_create = service._panel_client, service.panel_create_user
    service._panel_client = lambda: panel

    async def create_then_lose(*args, **kwargs):
        await old_create(*args, **kwargs)
        raise RuntimeError("trial create response lost")

    service.panel_create_user = create_then_lose
    try:
        with pytest.raises(RuntimeError, match="response lost"):
            await service.claim_test_sub(_context(pool), shop, 111, "buyer")
        saved = await pool.fetchrow(
            "SELECT * FROM plugin_pershop_test_claims WHERE tg_id=111")
        stage = json.loads(saved["provision_stage"])
        assert stage["state"] == "creating" and stage["attempt"] is None
        original_request = stage["request"]
        assert saved["provision_username"] == "vip-Test-1"
        assert await pool.fetchval("SELECT COUNT(*) FROM plugin_pershop_subs") == 0
        service.panel_create_user = old_create
        result = await service.claim_test_sub(_context(pool), shop, 111, "buyer")
        with pytest.raises(ValueError, match="already claimed"):
            await service.claim_test_sub(_context(pool), shop, 111, "buyer")
    finally:
        service.panel_create_user = old_create
        service._panel_client = old_client
    assert result["username"] == original_request["username"] == "vip-Test-1"
    assert result["user_uuid"] == "uuid-1"
    assert result["expire_at"] == datetime.fromisoformat(original_request["expire_at"])
    assert len(panel.created) == 1
    assert await pool.fetchval("SELECT COUNT(*) FROM users") == 1
    assert await pool.fetchval("SELECT COUNT(*) FROM plugin_pershop_subs") == 1
    assert await pool.fetchval(
        "SELECT test_counter FROM plugin_pershop_shops WHERE id=$1", shop["id"]) == 1


@pg_test
async def test_trial_uncertain_absence_fails_closed_without_second_create(pool):
    from rwa_plugin_pershop import service

    await _core_users(pool)
    shop = await _trial_shop(pool)
    panel = ApprovalPanel(pool)
    old_client, old_create = service._panel_client, service.panel_create_user
    service._panel_client = lambda: panel

    async def fail_before_create(*args, **kwargs):
        raise RuntimeError("panel unavailable before create")

    service.panel_create_user = fail_before_create
    try:
        with pytest.raises(RuntimeError, match="before create"):
            await service.claim_test_sub(_context(pool), shop, 111, "buyer")
        saved = await pool.fetchrow(
            "SELECT * FROM plugin_pershop_test_claims WHERE tg_id=111")
        original = saved["provision_stage"]
        assert json.loads(original)["request"]["username"] == "vip-Test-1"
        with pytest.raises(RuntimeError, match="requires reconciliation"):
            await service.claim_test_sub(_context(pool), shop, 111, "buyer")
    finally:
        service.panel_create_user = old_create
        service._panel_client = old_client
    assert len(panel.created) == 0
    assert await pool.fetchval(
        "SELECT provision_stage FROM plugin_pershop_test_claims WHERE tg_id=111") == original
    assert await pool.fetchval(
        "SELECT provision_username FROM plugin_pershop_test_claims WHERE tg_id=111"
    ) == "vip-Test-1"
    assert await pool.fetchval("SELECT COUNT(*) FROM plugin_pershop_subs") == 0
    assert await pool.fetchval("SELECT COUNT(*) FROM users") == 0
    assert await pool.fetchval(
        "SELECT test_counter FROM plugin_pershop_shops WHERE id=$1", shop["id"]) == 1


async def _trial_collision(pool, *, incomplete):
    from rwa_plugin_pershop import service

    await _core_users(pool)
    shop = await _trial_shop(pool)
    panel = ApprovalPanel(pool)
    old_client, old_create = service._panel_client, service.panel_create_user
    service._panel_client = lambda: panel

    async def interrupt_before_create(*args, **kwargs):
        raise RuntimeError("interrupted")

    service.panel_create_user = interrupt_before_create
    try:
        with pytest.raises(RuntimeError, match="interrupted"):
            await service.claim_test_sub(_context(pool), shop, 111, "buyer")
        stage = json.loads(await pool.fetchval(
            "SELECT provision_stage FROM plugin_pershop_test_claims WHERE tg_id=111"))
        request = stage["request"]
        await pool.execute(
            """INSERT INTO users(uuid,username,telegram_id,description)
               VALUES('foreign-uuid',$1,$2,$3)""", request["username"],
            None if incomplete else 222,
            request["description"] if incomplete else "another request")
        service.panel_create_user = old_create
        with pytest.raises(RuntimeError, match="belongs to another account"):
            await service.claim_test_sub(_context(pool), shop, 111, "buyer")
    finally:
        service.panel_create_user = old_create
        service._panel_client = old_client
    assert len(panel.created) == 0
    assert await pool.fetchval("SELECT COUNT(*) FROM users") == 1
    assert await pool.fetchval("SELECT COUNT(*) FROM plugin_pershop_subs") == 0
    saved = json.loads(await pool.fetchval(
        "SELECT provision_stage FROM plugin_pershop_test_claims WHERE tg_id=111"))
    assert saved["state"] == "creating" and saved["attempt"] is None
    assert saved["request"] == request
    assert await pool.fetchval(
        "SELECT provision_username FROM plugin_pershop_test_claims WHERE tg_id=111"
    ) == request["username"]


@pg_test
async def test_trial_foreign_exact_username_collision_fails_closed(pool):
    await _trial_collision(pool, incomplete=False)


@pg_test
async def test_trial_metadata_incomplete_exact_username_collision_fails_closed(pool):
    await _trial_collision(pool, incomplete=True)


@pg_test
async def test_trial_active_attempt_rejected_and_stale_attempt_adopts_create(pool):
    from rwa_plugin_pershop import service

    await _core_users(pool)
    shop = await _trial_shop(pool)
    panel = ApprovalPanel(pool)
    old_client, old_create = service._panel_client, service.panel_create_user
    service._panel_client = lambda: panel

    async def create_then_interrupt(ctx, **kwargs):
        await old_create(ctx, **kwargs)
        raise asyncio.CancelledError("trial worker interrupted after create")

    service.panel_create_user = create_then_interrupt
    try:
        with pytest.raises(asyncio.CancelledError, match="interrupted after create"):
            await service.claim_test_sub(_context(pool), shop, 111, "buyer")
        service.panel_create_user = old_create
        active_raw = await pool.fetchval(
            "SELECT provision_stage FROM plugin_pershop_test_claims WHERE tg_id=111")
        active = json.loads(active_raw)
        assert active["state"] == "creating" and active["attempt"]
        with pytest.raises(ValueError, match="already in progress"):
            await service.claim_test_sub(_context(pool), shop, 111, "buyer")
        assert await pool.fetchval(
            "SELECT provision_stage FROM plugin_pershop_test_claims WHERE tg_id=111"
        ) == active_raw
        active["locked_at"] = (
            datetime.now(timezone.utc)
            - timedelta(seconds=service.APPROVAL_ATTEMPT_STALE_SECONDS + 1)
        ).isoformat()
        await pool.execute(
            "UPDATE plugin_pershop_test_claims SET provision_stage=$1 "
            "WHERE tg_id=111 AND provision_stage=$2", json.dumps(active), active_raw)
        result = await service.claim_test_sub(_context(pool), shop, 111, "buyer")
    finally:
        service.panel_create_user = old_create
        service._panel_client = old_client
    assert result["username"] == "vip-Test-1" and result["user_uuid"] == "uuid-1"
    assert len(panel.created) == 1
    assert await pool.fetchval("SELECT COUNT(*) FROM users") == 1
    assert await pool.fetchval("SELECT COUNT(*) FROM plugin_pershop_subs") == 1


@pg_test
async def test_trial_provisioned_result_save_fault_retries_by_adoption(pool):
    from rwa_plugin_pershop import service

    await _core_users(pool)
    shop = await _trial_shop(pool)
    panel = ApprovalPanel(pool)
    old_client = service._panel_client
    service._panel_client = lambda: panel
    await pool.execute("""
        CREATE FUNCTION fail_trial_result() RETURNS trigger AS $$
        BEGIN
            IF NEW.provision_stage::jsonb->>'state'='provisioned'
               AND NOT (NEW.provision_stage::jsonb ? 'finalized') THEN
                RAISE EXCEPTION 'injected trial result-save fault';
            END IF;
            RETURN NEW;
        END
        $$ LANGUAGE plpgsql
    """)
    await pool.execute("""
        CREATE TRIGGER fail_trial_result BEFORE UPDATE OF provision_stage
        ON plugin_pershop_test_claims FOR EACH ROW EXECUTE FUNCTION fail_trial_result()
    """)
    try:
        with pytest.raises(Exception, match="injected trial result-save fault"):
            await service.claim_test_sub(_context(pool), shop, 111, "buyer")
        assert await pool.fetchval("SELECT COUNT(*) FROM users") == 1
        assert await pool.fetchval("SELECT COUNT(*) FROM plugin_pershop_subs") == 0
        await pool.execute(
            "DROP TRIGGER fail_trial_result ON plugin_pershop_test_claims")
        result = await service.claim_test_sub(_context(pool), shop, 111, "buyer")
    finally:
        service._panel_client = old_client
    assert result["user_uuid"] == "uuid-1"
    assert len(panel.created) == 1
    assert await pool.fetchval("SELECT COUNT(*) FROM users") == 1
    assert await pool.fetchval("SELECT COUNT(*) FROM plugin_pershop_subs") == 1
    assert await pool.fetchval(
        "SELECT test_counter FROM plugin_pershop_shops WHERE id=$1", shop["id"]) == 1


@pg_test
async def test_trial_finalization_rolls_back_and_resumes_saved_result(pool):
    from rwa_plugin_pershop import service

    await _core_users(pool)
    shop = await _trial_shop(pool)
    panel = ApprovalPanel(pool)
    old_client = service._panel_client
    service._panel_client = lambda: panel
    await pool.execute("""
        CREATE FUNCTION fail_trial_sub() RETURNS trigger AS $$
        BEGIN
            RAISE EXCEPTION 'injected trial finalization fault';
        END
        $$ LANGUAGE plpgsql
    """)
    await pool.execute("""
        CREATE TRIGGER fail_trial_sub BEFORE INSERT ON plugin_pershop_subs
        FOR EACH ROW WHEN (NEW.is_test) EXECUTE FUNCTION fail_trial_sub()
    """)
    try:
        with pytest.raises(Exception, match="injected trial finalization fault"):
            await service.claim_test_sub(_context(pool), shop, 111, "buyer")
        saved = json.loads(await pool.fetchval(
            "SELECT provision_stage FROM plugin_pershop_test_claims WHERE tg_id=111"))
        assert saved["state"] == "provisioned" and not saved.get("finalized")
        assert saved["result"]["user_uuid"] == "uuid-1"
        assert await pool.fetchval("SELECT COUNT(*) FROM plugin_pershop_subs") == 0
        await pool.execute("DROP TRIGGER fail_trial_sub ON plugin_pershop_subs")
        result = await service.claim_test_sub(_context(pool), shop, 111, "buyer")
    finally:
        service._panel_client = old_client
    assert result["user_uuid"] == "uuid-1"
    assert len(panel.created) == 1
    assert await pool.fetchval("SELECT COUNT(*) FROM plugin_pershop_subs") == 1
    assert await pool.fetchval(
        "SELECT provision_stage::jsonb->>'finalized' "
        "FROM plugin_pershop_test_claims WHERE tg_id=111") == "true"
    assert await pool.fetchval(
        "SELECT test_counter FROM plugin_pershop_shops WHERE id=$1", shop["id"]) == 1
