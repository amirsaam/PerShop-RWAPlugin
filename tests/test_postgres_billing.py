"""Financial/concurrency tests against a real, migrated PostgreSQL database."""
from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from decimal import Decimal
import functools
import inspect
import json
import uuid

import pytest

from postgres_support import Context, SERVER, migrated_database, new_order, seed_shop
from rwa_plugin_pershop import billing


from rwa_test_support import recognized_legacy_owner


def pg_test(function):
    @functools.wraps(function)
    def run():
        async def main():
            pool, name = await migrated_database()
            try:
                await function(pool)
            finally:
                await SERVER.drop(pool, name)
        with recognized_legacy_owner():
            asyncio.run(main())
    run.__signature__ = inspect.Signature()
    return run


@pg_test
async def test_same_key_and_balance_races(pool):
    """One key commits once; separate keys cannot overspend the wallet."""
    shop_id, package_id = await seed_shop(pool, credit="10")
    key = f"same-{uuid.uuid4().hex}"
    results = await asyncio.gather(*[
        new_order(pool, shop_id, package_id, key, credit="4")
        for _ in range(40)
    ])
    assert sum(bool(row["_created"]) for row in results) == 1
    assert await pool.fetchval("SELECT COUNT(*) FROM plugin_pershop_orders") == 1
    assert await pool.fetchval(
        "SELECT credit_units FROM plugin_pershop_buyers WHERE shop_id=$1",
        shop_id) == Decimal("6")
    debit = await pool.fetchrow(
        "SELECT * FROM plugin_pershop_credit_ledger WHERE shop_id=$1",
        shop_id)
    assert debit["kind"] == "debit" and debit["units"] == Decimal("-4")
    assert debit["order_id"] == results[0]["id"]

    outcomes = await asyncio.gather(*[
        new_order(pool, shop_id, package_id, f"race-{index}", credit="6", total="6")
        for index in range(16)
    ], return_exceptions=True)
    assert sum(not isinstance(row, Exception) for row in outcomes) == 1
    assert all(isinstance(row, dict) or isinstance(row, billing.InsufficientCredit)
               for row in outcomes)
    assert await pool.fetchval(
        "SELECT credit_units FROM plugin_pershop_buyers WHERE shop_id=$1",
        shop_id) == Decimal("0")
    assert await pool.fetchval("SELECT COUNT(*) FROM plugin_pershop_orders") == 2
    assert await pool.fetchval(
        "SELECT COUNT(*) FROM plugin_pershop_credit_ledger WHERE kind='checkout'",
    ) == 0


@pg_test
async def test_stale_checkout_reservation_can_retry(pool):
    """A crash after an unfunded reservation must not poison the key."""
    shop_id, package_id = await seed_shop(pool, credit="0")
    key = f"retry-{uuid.uuid4().hex}"
    await pool.execute(
        "INSERT INTO plugin_pershop_credit_ledger(shop_id,tg_id,kind,units,"
        "idempotency_key) VALUES($1,111,'checkout',0,$2)", shop_id, key)
    await pool.execute(
        "UPDATE plugin_pershop_buyers SET credit_units=4 WHERE shop_id=$1",
        shop_id)
    row = await new_order(pool, shop_id, package_id, key, credit="4")
    assert row["_created"] is True
    assert await pool.fetchval(
        "SELECT kind FROM plugin_pershop_credit_ledger WHERE shop_id=$1",
        shop_id) == "debit"


@pg_test
async def test_checkout_identity_collision_is_rejected(pool):
    shop_id, package_id = await seed_shop(pool)
    key = "identity"
    row = await new_order(pool, shop_id, package_id, key, credit="4")
    assert row["_created"] is True
    with pytest.raises(billing.CheckoutRejected):
        await new_order(pool, shop_id, package_id, key, buyer=222, credit="4")
    with pytest.raises(billing.CheckoutRejected):
        await new_order(pool, shop_id, package_id, key, credit="3")
    assert await pool.fetchval(
        "SELECT credit_units FROM plugin_pershop_buyers WHERE shop_id=$1 AND tg_id=111",
        shop_id) == Decimal("6")


@pg_test
async def test_parallel_denial_and_withdrawal_refund_once(pool):
    shop_id, package_id = await seed_shop(pool, credit="10")
    order = await new_order(pool, shop_id, package_id, "refund", credit="4")
    results = await asyncio.gather(
        billing.prefund_withdrawal(Context(pool), dict(order)),
        billing.prefund_withdrawal(Context(pool), dict(order)),
        billing.deny_order_atomic(Context(pool), dict(order), "owner", "bad", shop_id),
        billing.deny_order_atomic(Context(pool), dict(order), "owner", "bad", shop_id),
        return_exceptions=True)
    assert sum(isinstance(result, ValueError) for result in results) == 1
    assert await pool.fetchval(
        "SELECT status FROM plugin_pershop_orders WHERE id=$1", order["id"]) == "denied"
    assert await pool.fetchval(
        "SELECT credit_units FROM plugin_pershop_buyers WHERE shop_id=$1",
        shop_id) == Decimal("10")
    assert await pool.fetchval(
        "SELECT COUNT(*) FROM plugin_pershop_credit_ledger "
        "WHERE order_id=$1 AND units>0", order["id"]) == 1


@pg_test
async def test_withdrawal_keep_cannot_spend_refunded_credit(pool):
    shop_id, package_id = await seed_shop(pool, credit="10")
    order = await new_order(pool, shop_id, package_id, "keep", credit="4")
    await pool.execute(
        "UPDATE plugin_pershop_orders SET status='withdraw_requested' WHERE id=$1",
        order["id"])
    order = dict(await pool.fetchrow(
        "SELECT * FROM plugin_pershop_orders WHERE id=$1", order["id"]))
    refunded = await billing.prefund_withdrawal(Context(pool), order)
    assert refunded == Decimal("4")
    with pytest.raises(ValueError):
        await billing.resolve_withdrawal_atomic(Context(pool), order, "owner", False)
    assert await pool.fetchval(
        "SELECT status FROM plugin_pershop_orders WHERE id=$1", order["id"]
    ) == "withdraw_requested"


@pg_test
async def test_discount_races_and_failed_reservation_rollback(pool):
    shop_id, package_id = await seed_shop(pool, credit="20")
    await pool.execute(
        "UPDATE plugin_pershop_shops SET campaign_percent=15 WHERE id=$1", shop_id)
    await pool.execute(
        "INSERT INTO plugin_pershop_referrals(shop_id,referrer_tg_id,referred_tg_id)"
        " VALUES($1,777,111)", shop_id)
    await pool.execute(
        "INSERT INTO plugin_pershop_promos(shop_id,code,kind,value,max_uses)"
        " VALUES($1,'SAVE','percent',50,1)", shop_id)
    first = await new_order(pool, shop_id, package_id, "discount-a", total="4",
                            options_snapshot=json.dumps(
                                {"credit_used": "0", "promo_code": "SAVE",
                                 "campaign_units": "0.6"}))
    second = await new_order(pool, shop_id, package_id, "discount-b", total="4",
                             options_snapshot=json.dumps(
                                 {"credit_used": "0", "promo_code": "SAVE",
                                  "campaign_units": "0.6"}))
    results = await asyncio.gather(
        billing.reserve_order_discounts(Context(pool), dict(first), "pkg"),
        billing.reserve_order_discounts(Context(pool), dict(second), "pkg"),
        return_exceptions=True)
    assert sum(isinstance(result, billing.CheckoutRejected) for result in results) == 1
    assert await pool.fetchval(
        "SELECT COUNT(*) FROM plugin_pershop_promo_uses") == 1
    assert await pool.fetchval(
        "SELECT COUNT(*) FROM plugin_pershop_campaign_uses") == 1
    assert await pool.fetchval("SELECT uses_reserved FROM plugin_pershop_promos") == 1

    # Force only the campaign to fail. The promo reservation made by the same
    # call must roll back instead of leaking capacity.
    await pool.execute("DELETE FROM plugin_pershop_promo_uses")
    await pool.execute("UPDATE plugin_pershop_promos SET uses_reserved=0")
    await pool.execute(
        "UPDATE plugin_pershop_orders SET status='approved' WHERE id=$1", first["id"])
    third = await new_order(pool, shop_id, package_id, "discount-c", total="4",
                            options_snapshot=json.dumps(
                                {"credit_used": "0", "promo_code": "SAVE",
                                 "campaign_units": "0.6"}))
    with pytest.raises(billing.CheckoutRejected):
        await billing.reserve_order_discounts(Context(pool), dict(third), "pkg")
    assert await pool.fetchval(
        "SELECT COUNT(*) FROM plugin_pershop_promo_uses WHERE order_id=$1",
        third["id"]) == 0
    assert await pool.fetchval("SELECT uses_reserved FROM plugin_pershop_promos") == 0


@pg_test
async def test_referral_finalization_is_exactly_once(pool):
    shop_id, package_id = await seed_shop(pool, credit="0", referral_percent="10")
    await pool.execute(
        "INSERT INTO plugin_pershop_referrals(shop_id,referrer_tg_id,referred_tg_id)"
        " VALUES($1,777,111)", shop_id)
    order = await new_order(pool, shop_id, package_id, "referral", credit="0")
    await pool.execute(
        "UPDATE plugin_pershop_orders SET status='approving' WHERE id=$1", order["id"])
    results = await asyncio.gather(*[
        billing.finalize_approval(Context(pool), dict(order), "owner", 777, "1")
        for _ in range(12)
    ], return_exceptions=True)
    assert sum(isinstance(row, dict) for row in results) == 1
    assert await pool.fetchval(
        "SELECT credit_units FROM plugin_pershop_buyers WHERE shop_id=$1 AND tg_id=777",
        shop_id) == Decimal("1")
    assert await pool.fetchval(
        "SELECT COUNT(*) FROM plugin_pershop_credit_ledger WHERE kind='referral'",
    ) == 1


@pg_test
async def test_credit_cancellation_refunds_and_releases_once(pool):
    shop_id, package_id = await seed_shop(pool, credit="10")
    await pool.execute(
        "INSERT INTO plugin_pershop_promos(shop_id,code,kind,value,max_uses)"
        " VALUES($1,'SAVE','percent',50,2)", shop_id)
    await pool.execute(
        "UPDATE plugin_pershop_shops SET campaign_percent=15 WHERE id=$1", shop_id)
    await pool.execute(
        "INSERT INTO plugin_pershop_referrals(shop_id,referrer_tg_id,referred_tg_id)"
        " VALUES($1,777,111)", shop_id)
    order = await new_order(pool, shop_id, package_id, "cancel", credit="4",
                            options_snapshot=json.dumps(
                                {"credit_used": "4", "promo_code": "SAVE",
                                 "campaign_units": "0.6"}))
    await billing.reserve_order_discounts(Context(pool), dict(order), "pkg")
    await pool.execute(
        "UPDATE plugin_pershop_orders SET status='approved',receipt_kind='credit',"
        "renew_username='vip-alice' WHERE id=$1", order["id"])
    order = dict(await pool.fetchrow(
        "SELECT * FROM plugin_pershop_orders WHERE id=$1", order["id"]))
    sub_id = await pool.fetchval(
        "INSERT INTO plugin_pershop_subs(shop_id,buyer_tg_id,user_uuid,username)"
        " VALUES($1,111,'uuid-1','vip-alice') RETURNING id", shop_id)
    sub = dict(await pool.fetchrow(
        "SELECT * FROM plugin_pershop_subs WHERE id=$1", sub_id))
    results = await asyncio.gather(*[
        billing.cancel_credit_sub_atomic(Context(pool), sub, order, 111)
        for _ in range(8)
    ], return_exceptions=True)
    assert sum(isinstance(result, dict) for result in results) == 1
    assert await pool.fetchval(
        "SELECT credit_units FROM plugin_pershop_buyers WHERE shop_id=$1 AND tg_id=111",
        shop_id) == Decimal("10")
    assert await pool.fetchval("SELECT COUNT(*) FROM plugin_pershop_promo_uses") == 0
    assert await pool.fetchval("SELECT COUNT(*) FROM plugin_pershop_campaign_uses") == 0
    assert await pool.fetchval("SELECT uses_reserved FROM plugin_pershop_promos") == 0


@pg_test
async def test_update_claim_tokens_and_stale_takeover(pool):
    shop_id, _ = await seed_shop(pool)
    ctx = Context(pool)
    first = await billing.begin_update(ctx, shop_id, 100)
    assert isinstance(first, str)
    assert await billing.begin_update(ctx, shop_id, 100) is None
    await billing.finish_update(ctx, shop_id, 100, first)
    assert await billing.begin_update(ctx, shop_id, 100) is None
    with pytest.raises(ValueError):
        await billing.finish_update(ctx, shop_id, 100, first)

    await pool.execute("UPDATE plugin_pershop_updates SET processed_at=NULL")
    await billing.abandon_update(ctx, shop_id, 100, "not-the-owner")
    assert await pool.fetchval(
        "SELECT processed_at IS NULL FROM plugin_pershop_updates WHERE shop_id=$1",
        shop_id) is True

    await pool.execute(
        "UPDATE plugin_pershop_updates SET processed_at=NULL,locked_at=$1",
        datetime.now(timezone.utc) - timedelta(minutes=6))
    second = await billing.begin_update(ctx, shop_id, 100)
    assert isinstance(second, str) and second != first
    await billing.abandon_update(ctx, shop_id, 100, first)
    await billing.finish_update(ctx, shop_id, 100, second)
    row = await pool.fetchrow(
        "SELECT * FROM plugin_pershop_updates WHERE shop_id=$1", shop_id)
    assert row["processed_at"] is not None and row["owner_token"] == second


async def _core_users(pool):
    await pool.execute("""
        CREATE TABLE users (
            uuid TEXT PRIMARY KEY,
            username TEXT NOT NULL UNIQUE,
            subscription_url TEXT,
            expire_at TIMESTAMPTZ,
            traffic_limit_bytes BIGINT,
            telegram_id BIGINT,
            description TEXT,
            panel_id BIGINT UNIQUE,
            created_by_admin_id BIGINT,
            used_traffic_bytes BIGINT NOT NULL DEFAULT 0,
            active_internal_squads TEXT[] NOT NULL DEFAULT '{}'
        )
    """)


class ApprovalPanel:
    def __init__(self, pool):
        self.pool = pool
        self.created = []
        self.updated = []
        self.lose_update = False

    async def create_user(self, **kwargs):
        self.created.append(kwargs)
        number = len(self.created)
        return {"response": {
            "id": 1000 + number, "uuid": f"uuid-{number}",
            "username": kwargs["username"], "subscriptionUrl": "https://sub/x",
            "trafficLimitBytes": kwargs.get("traffic_limit_bytes"),
            "expireAt": datetime.fromisoformat(kwargs["expire_at"]),
            "telegramId": kwargs.get("telegram_id"),
            "description": kwargs.get("description"),
        }}

    async def update_user(self, user_uuid, **fields):
        self.updated.append((user_uuid, dict(fields)))
        assignments = []
        args = [user_uuid]
        for column, key in (("expire_at", "expireAt"),
                            ("traffic_limit_bytes", "trafficLimitBytes"),
                            ("telegram_id", "telegramId")):
            if key not in fields:
                continue
            value = fields[key]
            if column == "expire_at" and isinstance(value, str):
                value = datetime.fromisoformat(value.replace("Z", "+00:00"))
            args.append(value)
            assignments.append(f"{column}=${len(args)}")
        if assignments:
            await self.pool.execute(
                f"UPDATE users SET {','.join(assignments)} WHERE uuid=$1", *args)
        if self.lose_update:
            self.lose_update = False
            raise RuntimeError("response lost after renewal")


@pg_test
async def test_checkout_statement_fault_and_lost_commit_result(pool):
    """A failed ledger write rolls back the whole checkout; retries recover."""
    from rwa_plugin_pershop import billing as billing_module

    shop_id, package_id = await seed_shop(pool, credit="10")
    await pool.execute("""
        CREATE FUNCTION fail_checkout_ledger() RETURNS trigger AS $$
        BEGIN
            IF NEW.kind='debit' AND NEW.idempotency_key='fault' THEN
                RAISE EXCEPTION 'injected ledger fault';
            END IF;
            RETURN NEW;
        END
        $$ LANGUAGE plpgsql
    """)
    await pool.execute("""
        CREATE TRIGGER fail_checkout_ledger BEFORE UPDATE
        ON plugin_pershop_credit_ledger FOR EACH ROW
        EXECUTE FUNCTION fail_checkout_ledger()
    """)
    with pytest.raises(Exception, match="injected ledger fault"):
        await new_order(pool, shop_id, package_id, "fault", credit="4")
    assert await pool.fetchval("SELECT COUNT(*) FROM plugin_pershop_orders") == 0
    assert await pool.fetchval("SELECT COUNT(*) FROM plugin_pershop_credit_ledger") == 0
    assert await pool.fetchval(
        "SELECT credit_units FROM plugin_pershop_buyers WHERE shop_id=$1",
        shop_id) == Decimal("10")
    await pool.execute("DROP TRIGGER fail_checkout_ledger ON plugin_pershop_credit_ledger")
    await pool.execute("DROP FUNCTION fail_checkout_ledger()")
    row = await new_order(pool, shop_id, package_id, "fault", credit="4")
    assert row["_created"] is True

    original = billing_module.create_order_with_credit
    committed = {}

    async def lose_commit_result(*args, **kwargs):
        committed["row"] = await original(*args, **kwargs)
        raise RuntimeError("commit response lost")

    billing_module.create_order_with_credit = lose_commit_result
    try:
        with pytest.raises(RuntimeError, match="commit response lost"):
            await new_order(pool, shop_id, package_id, "committed", credit="4", total="4")
    finally:
        billing_module.create_order_with_credit = original
    replay = await new_order(pool, shop_id, package_id, "committed", credit="4", total="4")
    assert replay["id"] == committed["row"]["id"] and replay["_created"] is False
    assert await pool.fetchval("SELECT COUNT(*) FROM plugin_pershop_orders") == 2
    assert await pool.fetchval(
        "SELECT credit_units FROM plugin_pershop_buyers WHERE shop_id=$1",
        shop_id) == Decimal("2")
    assert await pool.fetchval(
        "SELECT COUNT(*) FROM plugin_pershop_credit_ledger WHERE kind='debit'") == 2


@pg_test
async def test_approval_has_one_claimant_and_one_panel_create(pool):
    from rwa_plugin_pershop import service

    await _core_users(pool)
    shop_id, package_id = await seed_shop(pool, credit="0")
    order = await new_order(pool, shop_id, package_id, "approval-race")
    panel = ApprovalPanel(pool)
    old_client = service._panel_client
    service._panel_client = lambda: panel
    try:
        results = await asyncio.gather(*[
            service.approve_order(Context(pool), order["id"], "owner", shop_id=shop_id)
            for _ in range(12)
        ], return_exceptions=True)
    finally:
        service._panel_client = old_client
    assert sum(isinstance(result, dict) for result in results) == 1, results
    assert len(panel.created) == 1
    assert await pool.fetchval("SELECT COUNT(*) FROM plugin_pershop_subs") == 1
    assert await pool.fetchval(
        "SELECT status FROM plugin_pershop_orders WHERE id=$1", order["id"]) == "approved"


@pg_test
async def test_approval_adopts_lost_create_without_repeating_it(pool):
    from rwa_plugin_pershop import service

    await _core_users(pool)
    shop_id, package_id = await seed_shop(pool, credit="0")
    order = await new_order(pool, shop_id, package_id, "lost-create")
    panel = ApprovalPanel(pool)
    old_client = service._panel_client
    old_create = service.panel_create_user
    service._panel_client = lambda: panel
    calls = 0

    async def create_then_lose(*args, **kwargs):
        nonlocal calls
        calls += 1
        await old_create(*args, **kwargs)
        raise RuntimeError("create response lost")

    service.panel_create_user = create_then_lose
    try:
        with pytest.raises(RuntimeError, match="create response lost"):
            await service.approve_order(Context(pool), order["id"], "owner", shop_id=shop_id)
        stage = json.loads(await pool.fetchval(
            "SELECT approval_stage FROM plugin_pershop_orders WHERE id=$1", order["id"]))
        assert stage["state"] == "creating" and stage["attempt"] is None
        assert stage["request"]["username"] == "vip-111"
        stored = await pool.fetchrow("SELECT * FROM users WHERE username='vip-111'")
        assert stored is not None and stored["description"] == "pershop:vip order #1", stored
    finally:
        service.panel_create_user = old_create
    try:
        result = await service.approve_order(
            Context(pool), order["id"], "owner", shop_id=shop_id)
    finally:
        service._panel_client = old_client
    assert calls == 1 and result["user_uuid"] == "uuid-1"
    assert await pool.fetchval("SELECT COUNT(*) FROM plugin_pershop_subs") == 1
    assert await pool.fetchval(
        "SELECT status FROM plugin_pershop_orders WHERE id=$1", order["id"]) == "approved"


@pg_test
async def test_saved_approval_result_resumes_rollback_and_referral_once(pool):
    from rwa_plugin_pershop import service

    await _core_users(pool)
    shop_id, package_id = await seed_shop(pool, credit="0", referral_percent="10")
    await pool.execute(
        "INSERT INTO plugin_pershop_referrals(shop_id,referrer_tg_id,referred_tg_id)"
        " VALUES($1,777,111)", shop_id)
    order = await new_order(pool, shop_id, package_id, "resume-finalize")
    panel = ApprovalPanel(pool)
    old_client = service._panel_client
    old_finalize = billing.finalize_approval
    service._panel_client = lambda: panel

    async def fail_finalization(*args, **kwargs):
        billing.finalize_approval = old_finalize
        raise RuntimeError("finalization connection lost")

    billing.finalize_approval = fail_finalization
    try:
        with pytest.raises(RuntimeError, match="finalization connection lost"):
            await service.approve_order(Context(pool), order["id"], "owner", shop_id=shop_id)
    finally:
        billing.finalize_approval = old_finalize
    assert len(panel.created) == 1
    assert await pool.fetchval("SELECT COUNT(*) FROM plugin_pershop_subs") == 0
    assert await pool.fetchval(
        "SELECT COUNT(*) FROM plugin_pershop_credit_ledger WHERE kind='referral'") == 0
    stage = json.loads(await pool.fetchval(
        "SELECT approval_stage FROM plugin_pershop_orders WHERE id=$1", order["id"]))
    assert stage["state"] == "provisioned"
    try:
        result = await service.approve_order(
            Context(pool), order["id"], "owner", shop_id=shop_id)
    finally:
        service._panel_client = old_client
    assert len(panel.created) == 1 and result["referrer_earned"] == 0.4
    assert await pool.fetchval("SELECT COUNT(*) FROM plugin_pershop_subs") == 1
    assert await pool.fetchval(
        "SELECT credit_units FROM plugin_pershop_buyers WHERE shop_id=$1 AND tg_id=777",
        shop_id) == Decimal("0.40")
    assert await pool.fetchval(
        "SELECT COUNT(*) FROM plugin_pershop_credit_ledger WHERE kind='referral'") == 1


@pg_test
async def test_prepanel_failure_releases_discount_and_allows_retry(pool):
    from rwa_plugin_pershop import service

    await _core_users(pool)
    shop_id, package_id = await seed_shop(pool, credit="0")
    await pool.execute(
        "INSERT INTO plugin_pershop_promos(shop_id,code,kind,value,max_uses)"
        " VALUES($1,'SAVE','percent',50,1)", shop_id)
    order = await new_order(
        pool, shop_id, package_id, "prepanel", options_snapshot=json.dumps(
            {"credit_used": "0", "promo_code": "SAVE"}))
    panel = ApprovalPanel(pool)
    old_client = service._panel_client
    old_count = service.count_promo_use
    service._panel_client = lambda: panel

    async def reserve_then_fail(ctx, current):
        await billing.reserve_order_discounts(ctx, dict(current), "pkg")
        raise RuntimeError("pre-panel failure")

    service.count_promo_use = reserve_then_fail
    try:
        with pytest.raises(RuntimeError, match="pre-panel failure"):
            await service.approve_order(Context(pool), order["id"], "owner", shop_id=shop_id)
    finally:
        service.count_promo_use = old_count
    assert await pool.fetchval(
        "SELECT status FROM plugin_pershop_orders WHERE id=$1", order["id"]) == "pending"
    assert await pool.fetchval("SELECT COUNT(*) FROM plugin_pershop_promo_uses") == 0
    assert await pool.fetchval("SELECT uses_reserved FROM plugin_pershop_promos") == 0
    try:
        await service.approve_order(Context(pool), order["id"], "owner", shop_id=shop_id)
    finally:
        service._panel_client = old_client
    assert await pool.fetchval(
        "SELECT status FROM plugin_pershop_orders WHERE id=$1", order["id"]) == "approved"
    assert await pool.fetchval("SELECT COUNT(*) FROM plugin_pershop_promo_uses") == 1


@pg_test
async def test_renewal_retry_reuses_absolute_expiry_target(pool):
    from rwa_plugin_pershop import service

    await _core_users(pool)
    shop_id, package_id = await seed_shop(pool, credit="0")
    start = datetime.now(timezone.utc).replace(microsecond=0) + timedelta(days=5)
    await pool.execute(
        "INSERT INTO users(uuid,username,expire_at,traffic_limit_bytes)"
        " VALUES('uuid-renew','vip-alice',$1,1000)", start)
    await pool.execute(
        "INSERT INTO plugin_pershop_subs(shop_id,buyer_tg_id,user_uuid,username,"
        "expire_at,traffic_limit_bytes,package_id) VALUES($1,111,'uuid-renew',"
        "'vip-alice',$2,1000,$3)", shop_id, start, package_id)
    order = await new_order(
        pool, shop_id, package_id, "renew-retry", renew_username="vip-alice")
    panel = ApprovalPanel(pool)
    panel.lose_update = True
    old_client = service._panel_client
    service._panel_client = lambda: panel
    try:
        with pytest.raises(RuntimeError, match="response lost after renewal"):
            await service.approve_order(Context(pool), order["id"], "owner", shop_id=shop_id)
        result = await service.approve_order(
            Context(pool), order["id"], "owner", shop_id=shop_id)
    finally:
        service._panel_client = old_client
    expiry_updates = [fields["expireAt"] for _, fields in panel.updated if "expireAt" in fields]
    assert len(expiry_updates) == 2 and expiry_updates[0] == expiry_updates[1]
    assert result["expire_at"] == start + timedelta(days=30)
    assert await pool.fetchval("SELECT expire_at FROM users WHERE uuid='uuid-renew'") == (
        start + timedelta(days=30))
    assert await pool.fetchval(
        "SELECT expire_at FROM plugin_pershop_subs WHERE username='vip-alice'") == (
        start + timedelta(days=30))


@pg_test
async def test_interrupted_preparing_approval_resumes_and_creates_once(pool):
    """Cancellation after the durable preparing claim is recoverable."""
    from rwa_plugin_pershop import service

    await _core_users(pool)
    shop_id, package_id = await seed_shop(pool, credit="0")
    order = await new_order(pool, shop_id, package_id, "preparing-interruption")
    panel = ApprovalPanel(pool)
    old_client = service._panel_client
    old_begin = service._begin_approval_attempt
    service._panel_client = lambda: panel

    async def interrupt_preparing(*args, **kwargs):
        raise asyncio.CancelledError("interrupted while preparing")

    service._begin_approval_attempt = interrupt_preparing
    try:
        with pytest.raises(asyncio.CancelledError, match="interrupted while preparing"):
            await service.approve_order(
                Context(pool), order["id"], "owner", shop_id=shop_id)
        saved = await pool.fetchrow(
            "SELECT status,approval_stage,provision_username "
            "FROM plugin_pershop_orders WHERE id=$1", order["id"])
        assert saved["status"] == "approving"
        assert json.loads(saved["approval_stage"]) == {"state": "preparing"}
        assert saved["provision_username"] is None
        assert len(panel.created) == 0

        service._begin_approval_attempt = old_begin
        result = await service.approve_order(
            Context(pool), order["id"], "owner", shop_id=shop_id)
    finally:
        service._begin_approval_attempt = old_begin
        service._panel_client = old_client
    assert result["user_uuid"] == "uuid-1"
    assert len(panel.created) == 1
    assert await pool.fetchval("SELECT COUNT(*) FROM users") == 1
    assert await pool.fetchval("SELECT COUNT(*) FROM plugin_pershop_subs") == 1
    assert await pool.fetchval(
        "SELECT status FROM plugin_pershop_orders WHERE id=$1", order["id"]) == "approved"


@pg_test
async def test_creating_retry_without_exact_user_fails_closed(pool):
    """An uncertain create without its exact mirror row must never be repeated."""
    from rwa_plugin_pershop import service

    await _core_users(pool)
    shop_id, package_id = await seed_shop(pool, credit="10")
    order = await new_order(pool, shop_id, package_id, "missing-created-user", credit="4")
    panel = ApprovalPanel(pool)
    old_client = service._panel_client
    old_create = service.panel_create_user
    service._panel_client = lambda: panel

    async def create_delete_mirror_and_lose(ctx, **kwargs):
        await old_create(ctx, **kwargs)
        await ctx.db.execute("DELETE FROM users WHERE username=$1", kwargs["username"])
        raise RuntimeError("create response and mirror row lost")

    service.panel_create_user = create_delete_mirror_and_lose
    try:
        with pytest.raises(RuntimeError, match="create response and mirror row lost"):
            await service.approve_order(
                Context(pool), order["id"], "owner", shop_id=shop_id)
        service.panel_create_user = old_create
        assert len(panel.created) == 1
        assert await pool.fetchval("SELECT COUNT(*) FROM users") == 0

        with pytest.raises(RuntimeError, match="provisioning outcome requires reconciliation"):
            await service.approve_order(
                Context(pool), order["id"], "owner", shop_id=shop_id)
    finally:
        service.panel_create_user = old_create
        service._panel_client = old_client
    saved = await pool.fetchrow(
        "SELECT * FROM plugin_pershop_orders WHERE id=$1", order["id"])
    stage = json.loads(saved["approval_stage"])
    assert saved["status"] == "approving" and stage["state"] == "creating"
    assert stage["request"]["username"] == "vip-111"
    assert saved["provision_username"] == "vip-111"
    assert saved["renew_username"] is None
    assert len(panel.created) == 1
    assert await pool.fetchval("SELECT COUNT(*) FROM plugin_pershop_subs") == 0
    assert await pool.fetchval(
        "SELECT credit_units FROM plugin_pershop_buyers WHERE shop_id=$1 AND tg_id=111",
        shop_id) == Decimal("6")
    assert await pool.fetchval(
        "SELECT COUNT(*) FROM plugin_pershop_credit_ledger "
        "WHERE order_id=$1 AND units>0", order["id"]) == 0


async def _assert_exact_collision_fails_closed(pool, *, incomplete):
    from rwa_plugin_pershop import service

    await _core_users(pool)
    shop_id, package_id = await seed_shop(pool, credit="0")
    order = await new_order(
        pool, shop_id, package_id,
        "incomplete-collision" if incomplete else "foreign-collision")
    panel = ApprovalPanel(pool)
    old_client = service._panel_client
    old_create = service.panel_create_user
    service._panel_client = lambda: panel

    async def create_then_lose(ctx, **kwargs):
        await old_create(ctx, **kwargs)
        raise RuntimeError("create response lost before collision check")

    service.panel_create_user = create_then_lose
    try:
        with pytest.raises(RuntimeError, match="create response lost before collision check"):
            await service.approve_order(
                Context(pool), order["id"], "owner", shop_id=shop_id)
        service.panel_create_user = old_create
        if incomplete:
            await pool.execute(
                "UPDATE users SET telegram_id=NULL WHERE username='vip-111'")
        else:
            await pool.execute(
                "UPDATE users SET description='another order',telegram_id=222 "
                "WHERE username='vip-111'")
        reservation = await pool.fetchval(
            "SELECT provision_username FROM plugin_pershop_orders WHERE id=$1", order["id"])

        with pytest.raises(RuntimeError, match="belongs to another account"):
            await service.approve_order(
                Context(pool), order["id"], "owner", shop_id=shop_id)
    finally:
        service.panel_create_user = old_create
        service._panel_client = old_client
    saved = await pool.fetchrow(
        "SELECT status,approval_stage,provision_username,renew_username "
        "FROM plugin_pershop_orders WHERE id=$1", order["id"])
    stage = json.loads(saved["approval_stage"])
    assert saved["status"] == "approving" and stage["state"] == "creating"
    assert stage["request"]["username"] == "vip-111"
    assert reservation == saved["provision_username"] == "vip-111"
    assert saved["renew_username"] is None
    assert len(panel.created) == 1
    assert await pool.fetchval("SELECT COUNT(*) FROM users") == 1
    assert await pool.fetchval("SELECT COUNT(*) FROM plugin_pershop_subs") == 0


@pg_test
async def test_foreign_exact_username_collision_fails_closed(pool):
    await _assert_exact_collision_fails_closed(pool, incomplete=False)


@pg_test
async def test_metadata_incomplete_exact_username_collision_fails_closed(pool):
    await _assert_exact_collision_fails_closed(pool, incomplete=True)


@pg_test
async def test_active_attempt_rejected_and_stale_attempt_adopts_create(pool):
    """Only a stale lock can yield, and takeover adopts the completed create."""
    from rwa_plugin_pershop import service

    await _core_users(pool)
    shop_id, package_id = await seed_shop(pool, credit="0")
    order = await new_order(pool, shop_id, package_id, "stale-creating-takeover")
    panel = ApprovalPanel(pool)
    old_client = service._panel_client
    old_create = service.panel_create_user
    service._panel_client = lambda: panel

    async def create_then_interrupt(ctx, **kwargs):
        await old_create(ctx, **kwargs)
        raise asyncio.CancelledError("worker interrupted after create")

    service.panel_create_user = create_then_interrupt
    try:
        with pytest.raises(asyncio.CancelledError, match="worker interrupted after create"):
            await service.approve_order(
                Context(pool), order["id"], "owner", shop_id=shop_id)
        service.panel_create_user = old_create
        active_raw = await pool.fetchval(
            "SELECT approval_stage FROM plugin_pershop_orders WHERE id=$1", order["id"])
        active = json.loads(active_raw)
        assert active["state"] == "creating" and active["attempt"]
        assert len(panel.created) == 1

        with pytest.raises(ValueError, match="approval is already in progress"):
            await service.approve_order(
                Context(pool), order["id"], "another-owner", shop_id=shop_id)
        assert await pool.fetchval(
            "SELECT approval_stage FROM plugin_pershop_orders WHERE id=$1",
            order["id"]) == active_raw
        assert len(panel.created) == 1

        active["locked_at"] = (
            datetime.now(timezone.utc)
            - timedelta(seconds=service.APPROVAL_ATTEMPT_STALE_SECONDS + 1)
        ).isoformat()
        stale_raw = json.dumps(active)
        await pool.execute(
            "UPDATE plugin_pershop_orders SET approval_stage=$1 "
            "WHERE id=$2 AND approval_stage=$3", stale_raw, order["id"], active_raw)
        result = await service.approve_order(
            Context(pool), order["id"], "recovery-owner", shop_id=shop_id)
    finally:
        service.panel_create_user = old_create
        service._panel_client = old_client
    assert result["user_uuid"] == "uuid-1"
    assert len(panel.created) == 1
    assert await pool.fetchval("SELECT COUNT(*) FROM users") == 1
    assert await pool.fetchval("SELECT COUNT(*) FROM plugin_pershop_subs") == 1
    assert await pool.fetchval(
        "SELECT status FROM plugin_pershop_orders WHERE id=$1", order["id"]) == "approved"


@pg_test
async def test_provisioned_result_save_fault_retries_by_adoption(pool):
    """A real PostgreSQL fault after create must not cause a second create."""
    from rwa_plugin_pershop import service

    await _core_users(pool)
    shop_id, package_id = await seed_shop(pool, credit="0")
    order = await new_order(pool, shop_id, package_id, "provisioned-save-fault")
    panel = ApprovalPanel(pool)
    old_client = service._panel_client
    service._panel_client = lambda: panel
    await pool.execute("""
        CREATE FUNCTION fail_provisioned_result() RETURNS trigger AS $$
        BEGIN
            IF NEW.approval_stage::jsonb->>'state' = 'provisioned' THEN
                RAISE EXCEPTION 'injected provisioned result-save fault';
            END IF;
            RETURN NEW;
        END
        $$ LANGUAGE plpgsql
    """)
    await pool.execute("""
        CREATE TRIGGER fail_provisioned_result BEFORE UPDATE OF approval_stage
        ON plugin_pershop_orders FOR EACH ROW
        EXECUTE FUNCTION fail_provisioned_result()
    """)
    try:
        with pytest.raises(Exception, match="injected provisioned result-save fault"):
            await service.approve_order(
                Context(pool), order["id"], "owner", shop_id=shop_id)
        stage = json.loads(await pool.fetchval(
            "SELECT approval_stage FROM plugin_pershop_orders WHERE id=$1", order["id"]))
        assert stage["state"] == "creating" and stage["attempt"] is None
        assert len(panel.created) == 1
        assert await pool.fetchval("SELECT COUNT(*) FROM users") == 1
    finally:
        await pool.execute(
            "DROP TRIGGER fail_provisioned_result ON plugin_pershop_orders")
        await pool.execute("DROP FUNCTION fail_provisioned_result()")
    try:
        result = await service.approve_order(
            Context(pool), order["id"], "owner", shop_id=shop_id)
    finally:
        service._panel_client = old_client
    assert result["user_uuid"] == "uuid-1"
    assert len(panel.created) == 1
    assert await pool.fetchval("SELECT COUNT(*) FROM users") == 1
    assert await pool.fetchval("SELECT COUNT(*) FROM plugin_pershop_subs") == 1
    assert await pool.fetchval(
        "SELECT status FROM plugin_pershop_orders WHERE id=$1", order["id"]) == "approved"


@pg_test
async def test_open_approval_username_reservation_race_is_case_insensitive(pool):
    """Two open approvals cannot reserve case variants of one username."""
    from rwa_plugin_pershop import service

    await _core_users(pool)
    shop_id, package_id = await seed_shop(pool, credit="0")
    first = await new_order(pool, shop_id, package_id, "reservation-upper")
    second = await new_order(pool, shop_id, package_id, "reservation-lower")
    await pool.execute(
        "UPDATE plugin_pershop_orders SET buyer_username='Alice' WHERE id=$1", first["id"])
    await pool.execute(
        "UPDATE plugin_pershop_orders SET buyer_username='alice' WHERE id=$1", second["id"])
    panel = ApprovalPanel(pool)
    old_client = service._panel_client
    old_begin = service._begin_approval_attempt
    service._panel_client = lambda: panel
    barrier = asyncio.Barrier(2)

    async def race_reservations(ctx, order, request=None):
        if service._decode_approval_stage(order).get("state") == "preparing":
            await barrier.wait()
        return await old_begin(ctx, order, request)

    service._begin_approval_attempt = race_reservations
    try:
        results = await asyncio.gather(*[
            service.approve_order(Context(pool), order_id, "owner", shop_id=shop_id)
            for order_id in (first["id"], second["id"])
        ], return_exceptions=True)
    finally:
        service._begin_approval_attempt = old_begin
        service._panel_client = old_client
    successes = [result for result in results if isinstance(result, dict)]
    errors = [result for result in results if isinstance(result, Exception)]
    assert len(successes) == 1 and len(errors) == 1, results
    assert getattr(errors[0], "constraint_name", None) == (
        "uq_pershop_orders_provision_username"), errors[0]
    # The winning reservation is cleared when that approval completes. The
    # unique-constraint failure proves the loser never held a second live
    # reservation; a later retry may leave at most its own one behind.
    reservations = await pool.fetch(
        "SELECT provision_username FROM plugin_pershop_orders "
        "WHERE status='approving' AND provision_username IS NOT NULL")
    assert len(reservations) <= 1
    assert all(row["provision_username"].lower() == "vip-alice"
               for row in reservations)
    assert await pool.fetchval(
        "SELECT COUNT(*) FROM plugin_pershop_orders WHERE status='approved'") == 1
    assert len(panel.created) == 1
    assert await pool.fetchval("SELECT COUNT(*) FROM plugin_pershop_subs") == 1
