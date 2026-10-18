"""Subscription events / activity feed.

pershop_001/002 are frozen (servers already run them) — new DDL lives here:
- plugin_pershop_events: one append-only row per interesting shop moment
  (order created, approved, denied, sub linked, promo burned, device
  reset, reissue, broadcast queued). No FK: history must survive even if
  a row it refers to is deleted. Owners see the feed in the panel UI.
"""
revision = "pershop_003"
down_revision = "pershop_002"
depends_on = None


def upgrade() -> None:
    from alembic import op

    op.execute("""
        CREATE TABLE IF NOT EXISTS plugin_pershop_events (
            id BIGSERIAL PRIMARY KEY,
            shop_id BIGINT NOT NULL DEFAULT 0,
            kind TEXT NOT NULL DEFAULT '',
            buyer_tg_id BIGINT,
            detail TEXT NOT NULL DEFAULT '',
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
        )
    """)
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_shop_events_shop "
        "ON plugin_pershop_events(shop_id, id DESC)")


def downgrade() -> None:
    from alembic import op

    op.execute("DROP TABLE IF EXISTS plugin_pershop_events")
