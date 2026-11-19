"""Durable reconciliation records for external user-update failures.

An uncertain traffic charge can restore a panel value and still leave the RWA
counter outcome unknown. The incident preserves the intended update, previous
value, attempted value and compensation result for an operator to resolve.
"""
revision = "pershop_009"
down_revision = "pershop_008"
depends_on = None


def upgrade() -> None:
    from alembic import op

    op.execute("""
        CREATE TABLE IF NOT EXISTS plugin_pershop_update_incidents (
            id BIGSERIAL PRIMARY KEY,
            shop_id BIGINT NOT NULL REFERENCES plugin_pershop_shops(id) ON DELETE CASCADE,
            user_uuid TEXT NOT NULL,
            username TEXT NOT NULL DEFAULT '',
            panel_id TEXT NOT NULL DEFAULT '',
            request TEXT NOT NULL,
            previous_traffic_limit_bytes BIGINT,
            attempted_traffic_limit_bytes BIGINT,
            quota_delta_bytes BIGINT NOT NULL DEFAULT 0,
            compensation TEXT NOT NULL DEFAULT 'not_attempted',
            reason TEXT NOT NULL,
            state TEXT NOT NULL DEFAULT 'reconciliation',
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            resolved_at TIMESTAMPTZ,
            resolved_by TEXT,
            resolution_note TEXT
        )
    """)
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_pershop_update_incidents_shop_state "
        "ON plugin_pershop_update_incidents(shop_id,state,created_at)")


def downgrade() -> None:
    from alembic import op

    op.execute("DROP TABLE IF EXISTS plugin_pershop_update_incidents")
