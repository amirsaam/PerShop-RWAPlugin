"""Durable idempotency keys and financial invariants.

pershop_006 introduces exactly-once checkout and wallet operations, Telegram
update deduplication, and concurrency-safe promotion/campaign redemption.
Do not modify earlier migrations: installations already run them.
"""
revision = "pershop_006"
down_revision = "pershop_005"
depends_on = None


def upgrade() -> None:
    from alembic import op

    op.execute("""
        CREATE TABLE IF NOT EXISTS plugin_pershop_credit_ledger (
            id BIGSERIAL PRIMARY KEY,
            shop_id BIGINT NOT NULL REFERENCES plugin_pershop_shops(id) ON DELETE CASCADE,
            tg_id BIGINT NOT NULL,
            order_id BIGINT REFERENCES plugin_pershop_orders(id) ON DELETE SET NULL,
            sub_id BIGINT REFERENCES plugin_pershop_subs(id) ON DELETE SET NULL,
            kind TEXT NOT NULL,
            units NUMERIC NOT NULL,
            idempotency_key TEXT NOT NULL,
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            UNIQUE(shop_id, idempotency_key)
        )
    """)
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_pershop_ledger_shop_tg "
        "ON plugin_pershop_credit_ledger(shop_id, tg_id)")
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_pershop_ledger_order_kind "
        "ON plugin_pershop_credit_ledger(order_id,kind) WHERE order_id IS NOT NULL")
    op.execute(
        "ALTER TABLE plugin_pershop_orders ADD COLUMN IF NOT EXISTS idempotency_key TEXT")
    op.execute("DROP INDEX IF EXISTS uq_pershop_orders_idempotency")
    op.execute(
        "CREATE UNIQUE INDEX uq_pershop_orders_idempotency "
        "ON plugin_pershop_orders(shop_id,idempotency_key)")
    op.execute(
        "ALTER TABLE plugin_pershop_orders ADD COLUMN IF NOT EXISTS approval_stage TEXT")
    op.execute(
        "ALTER TABLE plugin_pershop_orders ADD COLUMN IF NOT EXISTS provision_username TEXT")
    op.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS uq_pershop_orders_provision_username "
        "ON plugin_pershop_orders(lower(provision_username)) "
        "WHERE status='approving' AND provision_username IS NOT NULL")
    op.execute("""
        CREATE TABLE IF NOT EXISTS plugin_pershop_campaign_uses (
            shop_id BIGINT NOT NULL REFERENCES plugin_pershop_shops(id) ON DELETE CASCADE,
            buyer_tg_id BIGINT NOT NULL,
            order_id BIGINT NOT NULL UNIQUE REFERENCES plugin_pershop_orders(id) ON DELETE CASCADE,
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            PRIMARY KEY (shop_id, buyer_tg_id)
        )
    """)
    op.execute(
        "ALTER TABLE plugin_pershop_promos ADD COLUMN IF NOT EXISTS uses_reserved INTEGER NOT NULL DEFAULT 0")
    op.execute("UPDATE plugin_pershop_promos p SET uses_reserved=u.cnt "
               "FROM (SELECT promo_id, COUNT(*) AS cnt FROM plugin_pershop_promo_uses GROUP BY promo_id) u "
               "WHERE p.id=u.promo_id")
    op.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS uq_pershop_promo_use_order "
        "ON plugin_pershop_promo_uses(order_id) WHERE order_id IS NOT NULL")
    op.execute("""
        CREATE TABLE IF NOT EXISTS plugin_pershop_updates (
            shop_id BIGINT NOT NULL REFERENCES plugin_pershop_shops(id) ON DELETE CASCADE,
            update_id BIGINT NOT NULL,
            owner_token TEXT NOT NULL,
            locked_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            processed_at TIMESTAMPTZ,
            PRIMARY KEY (shop_id, update_id)
        )
    """)


def downgrade() -> None:
    from alembic import op

    op.execute("DROP TABLE IF EXISTS plugin_pershop_updates")
    op.execute("DROP INDEX IF EXISTS uq_pershop_promo_use_order")
    op.execute("DROP TABLE IF EXISTS plugin_pershop_campaign_uses")
    op.execute("ALTER TABLE plugin_pershop_promos DROP COLUMN IF EXISTS uses_reserved")
    op.execute("DROP INDEX IF EXISTS uq_pershop_orders_provision_username")
    op.execute("ALTER TABLE plugin_pershop_orders DROP COLUMN IF EXISTS provision_username")
    op.execute("ALTER TABLE plugin_pershop_orders DROP COLUMN IF EXISTS approval_stage")
    op.execute("DROP INDEX IF EXISTS uq_pershop_orders_idempotency")
    op.execute("ALTER TABLE plugin_pershop_orders DROP COLUMN IF EXISTS idempotency_key")
    op.execute("DROP TABLE IF EXISTS plugin_pershop_credit_ledger")
