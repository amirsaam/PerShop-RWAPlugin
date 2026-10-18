"""Promo codes + first-purchase campaign + shops.campaign_percent.

pershop_001 is frozen (servers already run it) — all new DDL lives here:
- plugin_pershop_promos: one promo code per shop (codes created by a shop
  owner only ever match inside their own shop, UNIQUE(shop_id, code));
- plugin_pershop_promo_uses: max-uses counters + per-buyer dedupe;
- plugin_pershop_shops.campaign_percent: first-purchase discount for
  invitees — the Ad-campaign lever, 0 = off.
"""
revision = "pershop_002"
down_revision = "pershop_001"
depends_on = None


def upgrade() -> None:
    from alembic import op

    op.execute("""
        CREATE TABLE IF NOT EXISTS plugin_pershop_promos (
            id BIGSERIAL PRIMARY KEY,
            shop_id BIGINT NOT NULL REFERENCES plugin_pershop_shops(id) ON DELETE CASCADE,
            code TEXT NOT NULL,
            kind TEXT NOT NULL DEFAULT 'percent',
            value NUMERIC NOT NULL DEFAULT 0,
            max_uses INTEGER NOT NULL DEFAULT 0,
            valid_until TIMESTAMPTZ,
            active BOOLEAN NOT NULL DEFAULT TRUE,
            created_by_owner TEXT NOT NULL DEFAULT '',
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            UNIQUE(shop_id, code)
        )
    """)
    op.execute("""
        CREATE TABLE IF NOT EXISTS plugin_pershop_promo_uses (
            id BIGSERIAL PRIMARY KEY,
            promo_id BIGINT NOT NULL REFERENCES plugin_pershop_promos(id) ON DELETE CASCADE,
            shop_id BIGINT NOT NULL DEFAULT 0,
            order_id BIGINT,
            buyer_tg_id BIGINT NOT NULL,
            used_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            UNIQUE(promo_id, buyer_tg_id)
        )
    """)
    op.execute(
        "ALTER TABLE plugin_pershop_shops ADD COLUMN IF NOT EXISTS campaign_percent "
        "NUMERIC NOT NULL DEFAULT 0")
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_shop_promos_shop ON plugin_pershop_promos(shop_id, active)")
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_shop_promo_uses_promo ON plugin_pershop_promo_uses(promo_id)")


def downgrade() -> None:
    from alembic import op

    op.execute("DROP TABLE IF EXISTS plugin_pershop_promo_uses")
    op.execute("DROP TABLE IF EXISTS plugin_pershop_promos")
    op.execute("ALTER TABLE plugin_pershop_shops DROP COLUMN IF EXISTS campaign_percent")
