"""Resumable, collision-safe trial provisioning.

The Telegram id remains the panel-wide one-trial key. Its durable journal keeps
an interrupted panel create attached to the original username and request.
"""
revision = "pershop_007"
down_revision = "pershop_006"
depends_on = None


def upgrade() -> None:
    from alembic import op

    op.execute(
        "ALTER TABLE plugin_pershop_test_claims ADD COLUMN IF NOT EXISTS provision_stage TEXT")
    op.execute(
        "ALTER TABLE plugin_pershop_test_claims ADD COLUMN IF NOT EXISTS provision_username TEXT")
    op.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS uq_pershop_test_provision_username "
        "ON plugin_pershop_test_claims(lower(provision_username)) "
        "WHERE provision_username IS NOT NULL")


def downgrade() -> None:
    from alembic import op

    op.execute("DROP INDEX IF EXISTS uq_pershop_test_provision_username")
    op.execute("ALTER TABLE plugin_pershop_test_claims DROP COLUMN IF EXISTS provision_username")
    op.execute("ALTER TABLE plugin_pershop_test_claims DROP COLUMN IF EXISTS provision_stage")
