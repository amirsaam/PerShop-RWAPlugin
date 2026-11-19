"""Recoverable, idempotent external API user creation.

The shop/key pair reserves an immutable request and one case-insensitive
username. The journal fences panel creation, ownership stamping, quota
accounting, subscription insertion, and the saved public response.
"""
revision = "pershop_008"
down_revision = "pershop_007"
depends_on = None


def upgrade() -> None:
    from alembic import op

    op.execute("""
        CREATE TABLE IF NOT EXISTS plugin_pershop_api_user_creates (
            id BIGSERIAL PRIMARY KEY,
            shop_id BIGINT NOT NULL REFERENCES plugin_pershop_shops(id) ON DELETE CASCADE,
            idempotency_key TEXT NOT NULL,
            request TEXT NOT NULL,
            stage TEXT NOT NULL,
            provision_username TEXT NOT NULL,
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            UNIQUE(shop_id, idempotency_key)
        )
    """)
    op.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS uq_pershop_api_create_username "
        "ON plugin_pershop_api_user_creates(lower(provision_username))")
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_pershop_api_creates_updated "
        "ON plugin_pershop_api_user_creates(updated_at)")


def downgrade() -> None:
    from alembic import op

    op.execute("DROP TABLE IF EXISTS plugin_pershop_api_user_creates")
