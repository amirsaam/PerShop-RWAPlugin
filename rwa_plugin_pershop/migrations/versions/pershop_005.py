"""Top-up ⇄ package links + buyers' language preference.

pershop_001..004 are frozen (servers already run them) — new DDL lives here:
- plugin_pershop_topup_packages: which packages a top-up offer applies to.
  A top-up with no links stays visible to every subscriber; with links it
  only shows for subscribers whose plan is one of them. The old
  packages.scope_tier_id (tier scope) still works for rows created before
  this revision.
- plugin_pershop_buyers.lang: was NOT NULL DEFAULT 'en'. The bot creates a
  buyer row for every buyer it messages (message tracking), so the column
  default silently pinned every buyer to English and overrode the shop's
  language. The column is now nullable with no default: NULL = "the buyer
  never picked a language" (the shop's language applies), a value = an
  explicit choice. Existing 'en' rows are reset to NULL (they were
  indistinguishable from auto-created rows); those buyers follow the shop
  language again and can pick a language once more.
"""
revision = "pershop_005"
down_revision = "pershop_004"
depends_on = None


def upgrade() -> None:
    from alembic import op

    op.execute("""
        CREATE TABLE IF NOT EXISTS plugin_pershop_topup_packages (
            topup_id BIGINT NOT NULL REFERENCES plugin_pershop_packages(id) ON DELETE CASCADE,
            package_id BIGINT NOT NULL REFERENCES plugin_pershop_packages(id) ON DELETE CASCADE,
            PRIMARY KEY (topup_id, package_id)
        )
    """)
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_shop_topup_packages_pkg "
        "ON plugin_pershop_topup_packages(package_id)")
    op.execute("ALTER TABLE plugin_pershop_buyers ALTER COLUMN lang DROP DEFAULT")
    op.execute("ALTER TABLE plugin_pershop_buyers ALTER COLUMN lang DROP NOT NULL")
    op.execute("UPDATE plugin_pershop_buyers SET lang = NULL WHERE lang = 'en'")


def downgrade() -> None:
    from alembic import op

    op.execute("UPDATE plugin_pershop_buyers SET lang = 'en' WHERE lang IS NULL")
    op.execute("ALTER TABLE plugin_pershop_buyers ALTER COLUMN lang SET DEFAULT 'en'")
    op.execute("ALTER TABLE plugin_pershop_buyers ALTER COLUMN lang SET NOT NULL")
    op.execute("DROP TABLE IF EXISTS plugin_pershop_topup_packages")
