"""Tier visibility toggle, package sort order, last bot message tracking.

- shops.tiers_internal: tiers stay a grouping/squad tool, buyers see a flat package list.
- packages.sort: owner-controlled show order in the bot (ORDER BY sort, id).
- buyers.last_mid: last bot message id per buyer, for delete-before-send cleanup.
"""
revision = "pershop_002"
down_revision = "pershop_001"
# NOTE: branch_labels lives only on the branch root (pershop_001).
# Re-declaring it here makes alembic abort at script-parse time with
# "Branch name 'plugin_pershop' ... already used".
depends_on = None


def upgrade() -> None:
    from alembic import op

    op.execute("""
        ALTER TABLE plugin_pershop_shops
        ADD COLUMN IF NOT EXISTS tiers_internal BOOLEAN NOT NULL DEFAULT FALSE
    """)
    op.execute("""
        ALTER TABLE plugin_pershop_packages
        ADD COLUMN IF NOT EXISTS sort INTEGER NOT NULL DEFAULT 0
    """)
    op.execute("""
        ALTER TABLE plugin_pershop_buyers
        ADD COLUMN IF NOT EXISTS last_mid BIGINT
    """)


def downgrade() -> None:
    from alembic import op

    op.execute("ALTER TABLE plugin_pershop_buyers DROP COLUMN IF EXISTS last_mid")
    op.execute("ALTER TABLE plugin_pershop_packages DROP COLUMN IF EXISTS sort")
    op.execute("ALTER TABLE plugin_pershop_shops DROP COLUMN IF EXISTS tiers_internal")
