"""Promo scope: which kind of purchase a code may pay for.

pershop_001/002/003 are frozen (servers already run them) — new DDL lives
here:
- plugin_pershop_promos.applies_to: 'both' (default, every existing code),
  'packages' or 'services'. The bot refuses a code whose scope does not
  cover the contract the buyer is paying for.
"""
revision = "pershop_004"
down_revision = "pershop_003"
depends_on = None


def upgrade() -> None:
    from alembic import op

    op.execute(
        "ALTER TABLE plugin_pershop_promos "
        "ADD COLUMN IF NOT EXISTS applies_to TEXT NOT NULL DEFAULT 'both'")


def downgrade() -> None:
    from alembic import op

    op.execute(
        "ALTER TABLE plugin_pershop_promos DROP COLUMN IF EXISTS applies_to")
