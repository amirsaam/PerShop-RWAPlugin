"""Atomic checkout, wallet ledger, discounts, and update reservations.

Each public database operation commits its financial state in one statement.
Unique ledger keys make retries harmless; no caller has to infer that a wallet
update succeeded from a balance read.
"""
from __future__ import annotations

from contextlib import asynccontextmanager
from decimal import Decimal


@asynccontextmanager
async def _db_transaction(ctx):
    """Run related writes on one asyncpg connection and transaction.

    The small FakeDB used by the flow tests has no pool; yielding it keeps
    those tests useful without pretending that they prove database atomicity.
    Real PostgreSQL tests always exercise the connection-bound branch.
    """
    db = ctx.db
    acquire = getattr(db, "acquire", None)
    if callable(acquire):
        async with acquire() as connection:
            transaction = getattr(connection, "transaction", None)
            if callable(transaction):
                async with transaction():
                    yield connection
            else:
                yield connection
        return
    transaction = getattr(db, "transaction", None)
    if callable(transaction):
        async with transaction():
            yield db
        return
    yield db


class InsufficientCredit(ValueError):
    """The buyer no longer has enough credit for this checkout."""


class CheckoutRejected(ValueError):
    """A price discount is no longer eligible for this order."""


def _units(value) -> Decimal:
    amount = Decimal(str(value or 0))
    if not amount.is_finite():
        raise ValueError("credit amount must be finite")
    return amount


def _snapshot(order: dict) -> dict:
    import json

    raw = (order or {}).get("options_snapshot")
    try:
        value = json.loads(raw) if isinstance(raw, str) else (raw or {})
        return dict(value) if isinstance(value, dict) else {}
    except (TypeError, ValueError):
        return {}


def credit_spent(order: dict) -> Decimal:
    try:
        return max(Decimal(0), _units(_snapshot(order).get("credit_used")))
    except Exception:
        return Decimal(0)


def credit_prefunded(order: dict) -> bool:
    return bool(_snapshot(order).get("credit_prefunded"))


async def _refund_basis(ctx, order: dict, fallback=None) -> Decimal:
    """Prefer the immutable checkout debit; support pre-ledger orders too."""
    amount = await ctx.db.fetchval(
        """SELECT -units FROM plugin_pershop_credit_ledger
           WHERE shop_id=$1 AND order_id=$2 AND kind='debit' AND units<0
           ORDER BY id LIMIT 1""", order["shop_id"], order["id"])
    if amount is not None:
        return max(Decimal(0), _units(amount))
    if fallback is None:
        fallback = credit_spent(order)
    return max(Decimal(0), _units(fallback))


async def create_order_with_credit(
    ctx, *, shop_id: int, buyer_tg_id: int, buyer_username: str,
    package_id: int | None = None, service_id: int | None = None,
    receipt_file_id: str | None = None, receipt_kind: str = "text",
    renew_username: str | None = None, total_units=0,
    options_snapshot: str = "{}", credit_units=0,
    idempotency_key: str,
) -> dict:
    """Create an order and spend an exact credit amount atomically.

    The key normally contains the shop, Telegram chat, and incoming message
    or callback id. A replay returns the original order with _created=False.
    """
    amount = _units(credit_units)
    total = _units(total_units)
    if amount < 0 or total < 0 or amount > total:
        raise ValueError("credit amount is outside the order total")
    if not idempotency_key:
        raise ValueError("checkout idempotency key is required")
    async with _db_transaction(ctx) as db:
        # One loop also recovers reservations left by the pre-transaction
        # implementation. Fresh insufficient-funds attempts roll back here.
        for attempt in range(2):
            row = await db.fetchrow(
                """WITH reservation AS (
                       INSERT INTO plugin_pershop_credit_ledger(
                           shop_id,tg_id,kind,units,idempotency_key)
                       SELECT $1,$4,'checkout',0,$12
                       ON CONFLICT (shop_id,idempotency_key) DO NOTHING RETURNING id
                   ), wallet AS (
                       UPDATE plugin_pershop_buyers SET credit_units=credit_units-$11
                       WHERE shop_id=$1 AND tg_id=$4 AND credit_units >= $11
                         AND $11::numeric > 0
                         AND EXISTS (SELECT 1 FROM reservation)
                       RETURNING credit_units
                   ), created AS (
                       INSERT INTO plugin_pershop_orders(
                           shop_id,package_id,service_id,buyer_tg_id,buyer_username,
                           receipt_file_id,receipt_kind,renew_username,total_units,
                           options_snapshot,idempotency_key)
                       SELECT $1,$2::bigint,$3::bigint,$4,$5,$6,$7,$8,$9::numeric,$10,$12
                       WHERE EXISTS (SELECT 1 FROM reservation)
                         AND ($11::numeric = 0 OR EXISTS (SELECT 1 FROM wallet))
                       ON CONFLICT (shop_id,idempotency_key) DO NOTHING RETURNING *
                   )
                   SELECT * FROM created""",
                shop_id, package_id, service_id, int(buyer_tg_id), buyer_username or "",
                receipt_file_id, receipt_kind, renew_username, total,
                options_snapshot, amount, idempotency_key,
            )
            if row:
                await db.execute(
                    """UPDATE plugin_pershop_credit_ledger
                       SET order_id=$1,kind='debit',units=-$2::numeric
                       WHERE shop_id=$3 AND idempotency_key=$4 AND kind='checkout'""",
                    row["id"], amount, shop_id, idempotency_key)
                out = dict(row)
                out["_created"] = True
                return out
            existing = await db.fetchrow(
                "SELECT * FROM plugin_pershop_orders WHERE shop_id=$1 AND idempotency_key=$2",
                shop_id, idempotency_key)
            if existing:
                if (int(existing.get("buyer_tg_id") or 0) != int(buyer_tg_id)
                        or existing.get("package_id") != package_id
                        or existing.get("service_id") != service_id
                        or existing.get("receipt_file_id") != receipt_file_id
                        or existing.get("receipt_kind") != receipt_kind
                        or existing.get("renew_username") != renew_username
                        or _units(existing.get("total_units")) != total
                        or credit_spent(dict(existing)) != amount):
                    raise CheckoutRejected("idempotency key belongs to another checkout")
                await db.execute(
                    """UPDATE plugin_pershop_credit_ledger
                       SET order_id=$1,kind='debit',units=-$2::numeric
                       WHERE shop_id=$3 AND idempotency_key=$4""",
                    existing["id"], amount, shop_id, idempotency_key)
                out = dict(existing)
                out["_created"] = False
                return out
            await db.execute(
                """DELETE FROM plugin_pershop_credit_ledger
                   WHERE shop_id=$1 AND idempotency_key=$2
                     AND kind='checkout' AND order_id IS NULL""",
                shop_id, idempotency_key)
            if attempt == 0:
                continue
            raise InsufficientCredit("insufficient credit")
    raise InsufficientCredit("insufficient credit")


async def grant_credit_once(ctx, shop_id: int, tg_id: int, units, *,
                            kind: str, idempotency_key: str,
                            order_id: int | None = None,
                            sub_id: int | None = None) -> dict:
    """Grant credit once and return {applied, balance, amount}."""
    amount = _units(units)
    if not idempotency_key:
        raise ValueError("credit idempotency key is required")
    if amount < 0:
        raise ValueError("grant amount cannot be negative")
    if amount == 0:
        balance = await ctx.db.fetchval(
            "SELECT COALESCE(credit_units,0) FROM plugin_pershop_buyers WHERE shop_id=$1 AND tg_id=$2",
            shop_id, int(tg_id))
        return {"applied": False, "balance": balance or 0, "amount": Decimal(0)}
    row = await ctx.db.fetchrow(
        """WITH reservation AS (
               INSERT INTO plugin_pershop_credit_ledger(
                   shop_id,tg_id,order_id,sub_id,kind,units,idempotency_key)
               SELECT $1,$2,$3::bigint,$4::bigint,$5,$6::numeric,$7
               ON CONFLICT (shop_id,idempotency_key) DO NOTHING RETURNING id
           ), wallet AS (
               INSERT INTO plugin_pershop_buyers(shop_id,tg_id,credit_units)
               SELECT $1,$2,$6::numeric WHERE EXISTS (SELECT 1 FROM reservation)
               ON CONFLICT (shop_id,tg_id) DO UPDATE
               SET credit_units=plugin_pershop_buyers.credit_units+EXCLUDED.credit_units
               RETURNING credit_units
           )
           SELECT (SELECT credit_units FROM wallet) AS balance,
                  EXISTS (SELECT 1 FROM reservation) AS applied""",
        shop_id, int(tg_id), order_id, sub_id, kind, amount, idempotency_key,
    )
    balance = (row or {}).get("balance")
    if balance is None:
        balance = await ctx.db.fetchval(
            "SELECT COALESCE(credit_units,0) FROM plugin_pershop_buyers WHERE shop_id=$1 AND tg_id=$2",
            shop_id, int(tg_id))
    applied = bool((row or {}).get("applied"))
    amount_applied = amount if applied else Decimal(0)
    if not applied:
        existing = await ctx.db.fetchrow(
            """SELECT tg_id,order_id,sub_id,kind,units
               FROM plugin_pershop_credit_ledger
               WHERE shop_id=$1 AND idempotency_key=$2""",
            shop_id, idempotency_key)
        if (existing and (int(existing.get("tg_id") or 0) != int(tg_id)
                          or existing.get("kind") != kind
                          or _units(existing.get("units")) != amount
                          or existing.get("order_id") != order_id
                          or existing.get("sub_id") != sub_id)):
            raise CheckoutRejected("credit idempotency key belongs to another operation")
    return {"applied": applied, "balance": balance or 0,
            "amount": amount_applied}


async def prefund_withdrawal(ctx, order: dict) -> Decimal:
    """Return an order's credit slice once and stamp its snapshot atomically."""
    amount = await _refund_basis(ctx, order)
    if amount <= 0:
        return Decimal(0)
    key = f"refund:order:{order['id']}"
    row = await ctx.db.fetchrow(
        """WITH eligible AS (
               SELECT id,shop_id,buyer_tg_id FROM plugin_pershop_orders
               WHERE id=$1 AND shop_id=$2 AND buyer_tg_id=$3
                 AND status IN ('pending','withdraw_requested')
           ), reservation AS (
               INSERT INTO plugin_pershop_credit_ledger(
                   shop_id,tg_id,order_id,kind,units,idempotency_key)
               SELECT shop_id,buyer_tg_id,id,'refund',$4::numeric,$5
               FROM eligible ON CONFLICT (shop_id,idempotency_key) DO NOTHING
               RETURNING id,units
           ), wallet AS (
               INSERT INTO plugin_pershop_buyers(shop_id,tg_id,credit_units)
               SELECT $2,$3,$4::numeric WHERE EXISTS (SELECT 1 FROM reservation)
               ON CONFLICT (shop_id,tg_id) DO UPDATE
               SET credit_units=plugin_pershop_buyers.credit_units+EXCLUDED.credit_units
               RETURNING credit_units
           ), stamped AS (
               UPDATE plugin_pershop_orders o
               SET options_snapshot=jsonb_set(
                   COALESCE(NULLIF(o.options_snapshot,'')::jsonb,'{}'::jsonb),
                   '{credit_prefunded}','true'::jsonb)::text
               WHERE o.id=$1 AND EXISTS (SELECT 1 FROM reservation)
               RETURNING o.id
           )
           SELECT COALESCE((SELECT units FROM reservation),0) AS refunded""",
        order["id"], order["shop_id"], int(order["buyer_tg_id"]), amount, key)
    return _units((row or {}).get("refunded"))


async def deny_order_atomic(ctx, order: dict, decided_by: str, reason: str,
                            shop_id: int | None = None) -> Decimal:
    """Deny, refund, and release discount reservations in one statement."""
    amount = await _refund_basis(ctx, order)
    key = f"refund:order:{order['id']}"
    row = await ctx.db.fetchrow(
        """WITH decision AS (
               UPDATE plugin_pershop_orders SET status='denied',deny_reason=$2,
                   decided_at=NOW(),decided_by=$3
               WHERE id=$1 AND ($4::bigint IS NULL OR shop_id=$4)
                 AND status IN ('pending','withdraw_requested') RETURNING *
           ), reservation AS (
               INSERT INTO plugin_pershop_credit_ledger(
                   shop_id,tg_id,order_id,kind,units,idempotency_key)
               SELECT shop_id,buyer_tg_id,id,'refund',$5::numeric,$6
               FROM decision WHERE $5::numeric>0
               ON CONFLICT (shop_id,idempotency_key) DO NOTHING RETURNING id,units
           ), wallet AS (
               INSERT INTO plugin_pershop_buyers(shop_id,tg_id,credit_units)
               SELECT d.shop_id,d.buyer_tg_id,$5::numeric FROM decision d
               WHERE EXISTS (SELECT 1 FROM reservation)
               ON CONFLICT (shop_id,tg_id) DO UPDATE
               SET credit_units=plugin_pershop_buyers.credit_units+EXCLUDED.credit_units
               RETURNING credit_units
           ), promo_deleted AS (
               DELETE FROM plugin_pershop_promo_uses pu USING decision d
               WHERE pu.order_id=d.id RETURNING pu.promo_id
           ), promo_released AS (
               UPDATE plugin_pershop_promos p
               SET uses_reserved=GREATEST(0,p.uses_reserved-1)
               FROM promo_deleted pd WHERE p.id=pd.promo_id RETURNING p.id
           ), campaign_released AS (
               DELETE FROM plugin_pershop_campaign_uses cu USING decision d
               WHERE cu.order_id=d.id RETURNING cu.order_id
           )
           SELECT d.*,COALESCE((SELECT units FROM reservation),0) AS credit_refunded
           FROM decision d""",
        order["id"], (reason or "")[:1000], decided_by, shop_id, amount, key)
    if not row:
        raise ValueError("order was already decided")
    return _units(row.get("credit_refunded"))


async def resolve_withdrawal_atomic(ctx, order: dict, owner_admin: str,
                                    confirm: bool) -> dict:
    """Resolve a withdrawal and settle its credit exactly once."""
    if not confirm:
        refunded = await ctx.db.fetchval(
            """SELECT EXISTS (SELECT 1 FROM plugin_pershop_credit_ledger
               WHERE shop_id=$1 AND idempotency_key=$2 AND units>0)""",
            order["shop_id"], f"refund:order:{order['id']}")
        if refunded or credit_prefunded(order):
            raise ValueError("instantly refunded credit cannot return to an unpaid order")
    amount = await _refund_basis(ctx, order) if confirm else Decimal(0)
    key = f"refund:order:{order['id']}"
    row = await ctx.db.fetchrow(
        """WITH decision AS (
               UPDATE plugin_pershop_orders SET status=$2,decided_at=NOW(),decided_by=$3
               WHERE id=$1 AND shop_id=$4 AND status='withdraw_requested' RETURNING *
           ), reservation AS (
               INSERT INTO plugin_pershop_credit_ledger(
                   shop_id,tg_id,order_id,kind,units,idempotency_key)
               SELECT shop_id,buyer_tg_id,id,'refund',$5::numeric,$6
               FROM decision WHERE $5::numeric>0
               ON CONFLICT (shop_id,idempotency_key) DO NOTHING RETURNING id,units
           ), wallet AS (
               INSERT INTO plugin_pershop_buyers(shop_id,tg_id,credit_units)
               SELECT d.shop_id,d.buyer_tg_id,$5::numeric FROM decision d
               WHERE EXISTS (SELECT 1 FROM reservation)
               ON CONFLICT (shop_id,tg_id) DO UPDATE
               SET credit_units=plugin_pershop_buyers.credit_units+EXCLUDED.credit_units
               RETURNING credit_units
           ), promo_deleted AS (
               DELETE FROM plugin_pershop_promo_uses pu USING decision d
               WHERE pu.order_id=d.id AND $7 RETURNING pu.promo_id
           ), promo_released AS (
               UPDATE plugin_pershop_promos p
               SET uses_reserved=GREATEST(0,p.uses_reserved-1)
               FROM promo_deleted pd WHERE p.id=pd.promo_id RETURNING p.id
           ), campaign_released AS (
               DELETE FROM plugin_pershop_campaign_uses cu USING decision d
               WHERE cu.order_id=d.id AND $7 RETURNING cu.order_id
           )
           SELECT d.*,COALESCE((SELECT units FROM reservation),0) AS credit_refunded
           FROM decision d""",
        order["id"], "withdrawn" if confirm else "pending", owner_admin,
        order["shop_id"], amount, key, bool(confirm))
    if not row:
        raise ValueError("order was already decided")
    return dict(row)


async def cancel_credit_sub_atomic(ctx, sub: dict, order: dict,
                                   buyer_tg_id: int) -> dict:
    """Claim a subscription cancellation and its full-credit refund atomically."""
    amount = await _refund_basis(
        ctx, order, credit_spent(order) or _units(order.get("total_units")))
    row = await ctx.db.fetchrow(
        """WITH decision AS (
               UPDATE plugin_pershop_subs s SET cancelled=TRUE,updated_at=NOW()
               WHERE s.id=$1 AND s.buyer_tg_id=$2 AND NOT s.cancelled
                 AND EXISTS (
                     SELECT 1 FROM plugin_pershop_orders o
                     WHERE o.id=$3 AND o.shop_id=s.shop_id AND o.buyer_tg_id=$2
                       AND o.renew_username=s.username AND o.status='approved'
                       AND o.receipt_kind='credit')
               RETURNING s.*
           ), reservation AS (
               INSERT INTO plugin_pershop_credit_ledger(
                   shop_id,tg_id,order_id,sub_id,kind,units,idempotency_key)
               SELECT shop_id,$2,$3,id,'cancel_refund',$4::numeric,$5
               FROM decision WHERE $4::numeric>0
               ON CONFLICT (shop_id,idempotency_key) DO NOTHING RETURNING id,units
           ), wallet AS (
               INSERT INTO plugin_pershop_buyers(shop_id,tg_id,credit_units)
               SELECT d.shop_id,$2,$4::numeric FROM decision d
               WHERE EXISTS (SELECT 1 FROM reservation)
               ON CONFLICT (shop_id,tg_id) DO UPDATE
               SET credit_units=plugin_pershop_buyers.credit_units+EXCLUDED.credit_units
               RETURNING credit_units
           ), promo_deleted AS (
               DELETE FROM plugin_pershop_promo_uses pu USING decision d
               WHERE pu.order_id=$3 RETURNING pu.promo_id
           ), promo_released AS (
               UPDATE plugin_pershop_promos p
               SET uses_reserved=GREATEST(0,p.uses_reserved-1)
               FROM promo_deleted pd WHERE p.id=pd.promo_id RETURNING p.id
           ), campaign_released AS (
               DELETE FROM plugin_pershop_campaign_uses cu USING decision d
               WHERE cu.order_id=$3 RETURNING cu.order_id
           )
           SELECT d.id AS sub_id,COALESCE((SELECT units FROM reservation),0) AS refunded
           FROM decision d""",
        sub["id"], int(buyer_tg_id), order["id"], amount,
        f"refund:order:{order['id']}")
    if not row:
        raise ValueError("already")
    return {"sub_id": row["sub_id"], "amount": _units(row.get("refunded"))}


async def reserve_order_discounts(ctx, order: dict, kind: str | None = None) -> None:
    """Atomically validate and consume an order's promo and campaign.

    Rows are tied to the order so a retry recognizes its own reservation.
    The caller releases both if validation or a definite pre-panel step fails.
    """
    snap = _snapshot(order)
    code = str(snap.get("promo_code") or "").strip().upper()
    campaign = _units(snap.get("campaign_units")) > 0
    async with _db_transaction(ctx) as db:
        row = await db.fetchrow(
        """WITH claimed AS (
               UPDATE plugin_pershop_promos p SET uses_reserved=p.uses_reserved+1
               WHERE p.shop_id=$1 AND p.code=$2 AND $2<>'' AND p.active
                 AND (p.valid_until IS NULL OR p.valid_until>NOW())
                 AND ($6::text IS NULL OR
                      CASE p.applies_to WHEN 'packages' THEN $6='pkg'
                           WHEN 'services' THEN $6='svc' ELSE TRUE END)
                 AND (p.max_uses=0 OR p.uses_reserved<p.max_uses)
                 AND NOT EXISTS (
                     SELECT 1 FROM plugin_pershop_promo_uses old
                     WHERE old.promo_id=p.id AND old.buyer_tg_id=$3)
               RETURNING p.id
           ), promo_inserted AS (
               INSERT INTO plugin_pershop_promo_uses(promo_id,shop_id,order_id,buyer_tg_id)
               SELECT id,$1,$4,$3 FROM claimed
               ON CONFLICT (promo_id,buyer_tg_id) DO NOTHING RETURNING id
           ), campaign_inserted AS (
               INSERT INTO plugin_pershop_campaign_uses(shop_id,buyer_tg_id,order_id)
               SELECT s.id,$3,$4 FROM plugin_pershop_shops s
               WHERE s.id=$1 AND $5 AND s.campaign_percent>0
                 AND EXISTS (
                     SELECT 1 FROM plugin_pershop_referrals r
                     WHERE r.shop_id=s.id AND r.referred_tg_id=$3)
                 AND NOT EXISTS (
                     SELECT 1 FROM plugin_pershop_orders old
                     WHERE old.shop_id=s.id AND old.buyer_tg_id=$3
                       AND old.status='approved' AND old.id<>$4)
               ON CONFLICT (shop_id,buyer_tg_id) DO NOTHING RETURNING order_id
           )
           SELECT ($2='' OR EXISTS (SELECT 1 FROM promo_inserted) OR EXISTS (
                      SELECT 1 FROM plugin_pershop_promo_uses pu
                      JOIN plugin_pershop_promos p ON p.id=pu.promo_id
                      WHERE p.shop_id=$1 AND p.code=$2 AND pu.order_id=$4)) AS promo_ok,
                  (NOT $5 OR EXISTS (SELECT 1 FROM campaign_inserted) OR EXISTS (
                      SELECT 1 FROM plugin_pershop_campaign_uses cu
                      WHERE cu.shop_id=$1 AND cu.order_id=$4)) AS campaign_ok""",
        order["shop_id"], code, int(order["buyer_tg_id"]), order["id"],
        bool(campaign), kind)
        if not row or not row.get("promo_ok"):
            raise CheckoutRejected("promotion is no longer available")
        if not row.get("campaign_ok"):
            raise CheckoutRejected("first-order campaign is no longer available")


async def release_order_discounts(ctx, order_id: int) -> None:
    """Release reservations after denial or a definite pre-panel failure."""
    await ctx.db.execute(
        """WITH deleted AS (
               DELETE FROM plugin_pershop_promo_uses WHERE order_id=$1
               RETURNING promo_id
           ), released AS (
               UPDATE plugin_pershop_promos p
               SET uses_reserved=GREATEST(0,p.uses_reserved-1)
               FROM deleted d WHERE p.id=d.promo_id RETURNING p.id
           )
           DELETE FROM plugin_pershop_campaign_uses WHERE order_id=$1""",
        order_id)


async def finalize_approval(ctx, order: dict, decided_by: str,
                            referrer_tg_id: int | None, earned) -> dict:
    """Approve an order and pay its referral reward in the same statement."""
    amount = _units(earned)
    key = f"referral:order:{order['id']}"
    row = await ctx.db.fetchrow(
        """WITH decision AS (
               UPDATE plugin_pershop_orders SET status='approved',decided_at=NOW(),
                   decided_by=$2,credit_units=$4::numeric,approval_stage=NULL,
                   provision_username=NULL
               WHERE id=$1 AND status='approving' RETURNING *
           ), reservation AS (
               INSERT INTO plugin_pershop_credit_ledger(
                   shop_id,tg_id,order_id,kind,units,idempotency_key)
               SELECT shop_id,$3,id,'referral',$4::numeric,$5
               FROM decision WHERE $3::bigint IS NOT NULL AND $4::numeric>0
               ON CONFLICT (shop_id,idempotency_key) DO NOTHING RETURNING id,units
           ), wallet AS (
               INSERT INTO plugin_pershop_buyers(shop_id,tg_id,credit_units)
               SELECT d.shop_id,$3,$4::numeric FROM decision d
               WHERE EXISTS (SELECT 1 FROM reservation)
               ON CONFLICT (shop_id,tg_id) DO UPDATE
               SET credit_units=plugin_pershop_buyers.credit_units+EXCLUDED.credit_units
               RETURNING credit_units
           )
           SELECT d.*,COALESCE((SELECT units FROM reservation),0) AS referrer_earned
           FROM decision d""",
        order["id"], decided_by, referrer_tg_id, amount, key)
    if not row:
        raise ValueError("order was already decided")
    out = dict(row)
    out["referrer_earned"] = float(amount if referrer_tg_id else 0)
    return out


async def begin_update(ctx, shop_id: int, update_id: int | None):
    """Claim an update and return its ownership token, or None if claimed."""
    if update_id is None:
        return True
    import uuid

    token = uuid.uuid4().hex
    row = await ctx.db.fetchrow(
        """INSERT INTO plugin_pershop_updates(shop_id,update_id,owner_token)
           VALUES($1,$2,$3)
           ON CONFLICT (shop_id,update_id) DO UPDATE
           SET locked_at=NOW(),owner_token=EXCLUDED.owner_token
           WHERE plugin_pershop_updates.processed_at IS NULL
             AND plugin_pershop_updates.owner_token IS DISTINCT FROM EXCLUDED.owner_token
             AND plugin_pershop_updates.locked_at<NOW()-INTERVAL '5 minutes'
           RETURNING owner_token""", shop_id, update_id, token)
    return (row or {}).get("owner_token")


async def finish_update(ctx, shop_id: int, update_id: int | None,
                        owner_token) -> None:
    if update_id is not None:
        row = await ctx.db.fetchrow(
            """UPDATE plugin_pershop_updates SET processed_at=NOW()
               WHERE shop_id=$1 AND update_id=$2 AND owner_token=$3
                 AND processed_at IS NULL RETURNING update_id""",
            shop_id, update_id, owner_token)
        if not row:
            raise ValueError("update claim is no longer owned by this worker")


async def abandon_update(ctx, shop_id: int, update_id: int | None,
                         owner_token) -> None:
    """Release only this worker's failed claim so delivery can retry it."""
    if update_id is not None:
        await ctx.db.execute(
            """DELETE FROM plugin_pershop_updates
               WHERE shop_id=$1 AND update_id=$2 AND owner_token=$3
                 AND processed_at IS NULL""",
            shop_id, update_id, owner_token)
