"""Initial shop schema. Own Alembic branch: plugin_pershop.

Squashed pre-release (nothing installed yet): shops, tiers, packages,
orders, subs, sessions, referrals + notifier/order lookup indexes.
Post-release changes must go in NEW revisions (pershop_002, ...) for
compatibility with installed databases.
"""
revision = "pershop_001"
down_revision = None
branch_labels = ("plugin_pershop",)
depends_on = None


def upgrade() -> None:
    from alembic import op

    op.execute("""
        CREATE TABLE IF NOT EXISTS plugin_pershop_shops (
            id BIGSERIAL PRIMARY KEY,
            owner_admin TEXT NOT NULL UNIQUE,
            bot_token_enc TEXT,
            bot_username TEXT,
            admin_tg_id BIGINT NOT NULL,
            prefix TEXT NOT NULL UNIQUE,
            card_text TEXT NOT NULL DEFAULT '',
            welcome_text TEXT NOT NULL DEFAULT '',
            thank_you_text TEXT NOT NULL DEFAULT '',
            approved_text TEXT NOT NULL DEFAULT '',
            denied_text TEXT NOT NULL DEFAULT '',
            lang TEXT NOT NULL DEFAULT 'en',
            currency_label TEXT NOT NULL DEFAULT '',
            unit_rate NUMERIC,
            test_enabled BOOLEAN NOT NULL DEFAULT FALSE,
            test_counter INTEGER NOT NULL DEFAULT 0,
            referral_text TEXT NOT NULL DEFAULT '',
            referral_percent NUMERIC NOT NULL DEFAULT 0,
            support_text TEXT NOT NULL DEFAULT '',
            webhook_secret TEXT,
            pending_nag_hours INT NOT NULL DEFAULT 6,
            public_key_hash TEXT UNIQUE,
            public_key_enc TEXT,
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
        )
    """)
    op.execute("""
        CREATE TABLE IF NOT EXISTS plugin_pershop_tiers (
            id BIGSERIAL PRIMARY KEY,
            shop_id BIGINT NOT NULL REFERENCES plugin_pershop_shops(id) ON DELETE CASCADE,
            name TEXT NOT NULL,
            squad_uuid TEXT NOT NULL DEFAULT '',
            hwid_default INTEGER,
            sort INTEGER NOT NULL DEFAULT 0,
            active BOOLEAN NOT NULL DEFAULT TRUE,
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            UNIQUE(shop_id, name)
        )
    """)
    op.execute("""
        CREATE TABLE IF NOT EXISTS plugin_pershop_packages (
            id BIGSERIAL PRIMARY KEY,
            shop_id BIGINT NOT NULL REFERENCES plugin_pershop_shops(id) ON DELETE CASCADE,
            tier_id BIGINT REFERENCES plugin_pershop_tiers(id),
            name TEXT NOT NULL,
            traffic_limit_bytes BIGINT,
            duration_days INT NOT NULL DEFAULT 30,
            price_text TEXT NOT NULL DEFAULT '',
            price_units NUMERIC,
            squad_uuid TEXT,
            hwid_mode TEXT NOT NULL DEFAULT 'default',
            hwid_min INT NOT NULL DEFAULT 1,
            hwid_default INT NOT NULL DEFAULT 1,
            hwid_max INT NOT NULL DEFAULT 10,
            hwid_free INT NOT NULL DEFAULT 1,
            hwid_percent NUMERIC NOT NULL DEFAULT 50,
            hwid_fixed INT,
            traffic_reset BOOLEAN NOT NULL DEFAULT TRUE,
            effect TEXT NOT NULL DEFAULT 'full',
            scope_tier_id BIGINT REFERENCES plugin_pershop_tiers(id),
            active BOOLEAN NOT NULL DEFAULT TRUE,
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
        )
    """)
    op.execute("""
        CREATE TABLE IF NOT EXISTS plugin_pershop_service_cats (
            id BIGSERIAL PRIMARY KEY,
            shop_id BIGINT NOT NULL REFERENCES plugin_pershop_shops(id) ON DELETE CASCADE,
            name TEXT NOT NULL DEFAULT '',
            sort INT NOT NULL DEFAULT 0,
            active BOOLEAN NOT NULL DEFAULT TRUE,
            UNIQUE(shop_id, name)
        )
    """)
    op.execute("""
        CREATE TABLE IF NOT EXISTS plugin_pershop_services (
            id BIGSERIAL PRIMARY KEY,
            shop_id BIGINT NOT NULL REFERENCES plugin_pershop_shops(id) ON DELETE CASCADE,
            cat_id BIGINT REFERENCES plugin_pershop_service_cats(id) ON DELETE SET NULL,
            name TEXT NOT NULL DEFAULT '',
            price_units NUMERIC NOT NULL DEFAULT 0,
            description TEXT NOT NULL DEFAULT '',
            done_text TEXT NOT NULL DEFAULT '',
            active BOOLEAN NOT NULL DEFAULT TRUE,
            sort INT NOT NULL DEFAULT 0
        )
    """)
    op.execute("""
        CREATE TABLE IF NOT EXISTS plugin_pershop_orders (
            id BIGSERIAL PRIMARY KEY,
            shop_id BIGINT NOT NULL REFERENCES plugin_pershop_shops(id) ON DELETE CASCADE,
            package_id BIGINT REFERENCES plugin_pershop_packages(id),
            service_id BIGINT REFERENCES plugin_pershop_services(id),
            buyer_tg_id BIGINT NOT NULL,
            buyer_username TEXT NOT NULL DEFAULT '',
            receipt_file_id TEXT,
            receipt_kind TEXT NOT NULL DEFAULT 'text',
            renew_username TEXT,
            total_units NUMERIC,
            credit_units NUMERIC NOT NULL DEFAULT 0,
            options_snapshot TEXT,
            status TEXT NOT NULL DEFAULT 'pending',
            deny_reason TEXT,
            withdraw_reason TEXT,
            withdraw_card TEXT,
            owner_chat_id BIGINT,
            owner_msg_id BIGINT,
            buyer_msg_id BIGINT,
            owner_nudged_at TIMESTAMPTZ,
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            decided_at TIMESTAMPTZ,
            decided_by TEXT,
            CONSTRAINT ck_shop_orders_item CHECK (
                (package_id IS NOT NULL AND service_id IS NULL) OR
                (package_id IS NULL AND service_id IS NOT NULL)
            )
        )
    """)
    op.execute("""
        CREATE TABLE IF NOT EXISTS plugin_pershop_subs (
            id BIGSERIAL PRIMARY KEY,
            shop_id BIGINT NOT NULL REFERENCES plugin_pershop_shops(id) ON DELETE CASCADE,
            buyer_tg_id BIGINT,
            user_uuid TEXT NOT NULL,
            username TEXT NOT NULL UNIQUE,
            package_id BIGINT REFERENCES plugin_pershop_packages(id),
            expire_at TIMESTAMPTZ,
            traffic_limit_bytes BIGINT,
            is_test BOOLEAN NOT NULL DEFAULT FALSE,
            is_linked BOOLEAN NOT NULL DEFAULT FALSE,
            cancelled BOOLEAN NOT NULL DEFAULT FALSE,
            notified_80 BOOLEAN NOT NULL DEFAULT FALSE,
            notified_72h BOOLEAN NOT NULL DEFAULT FALSE,
            notified_expired BOOLEAN NOT NULL DEFAULT FALSE,
            notified_out BOOLEAN NOT NULL DEFAULT FALSE,
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
        )
    """)
    op.execute("""
        CREATE TABLE IF NOT EXISTS plugin_pershop_sessions (
            shop_id BIGINT NOT NULL REFERENCES plugin_pershop_shops(id) ON DELETE CASCADE,
            tg_id BIGINT NOT NULL,
            role TEXT NOT NULL,
            state TEXT NOT NULL DEFAULT 'idle',
            data TEXT NOT NULL DEFAULT '{}',
            updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            PRIMARY KEY (shop_id, tg_id, role)
        )
    """)
    op.execute("""
        CREATE TABLE IF NOT EXISTS plugin_pershop_referrals (
            id BIGSERIAL PRIMARY KEY,
            shop_id BIGINT NOT NULL REFERENCES plugin_pershop_shops(id) ON DELETE CASCADE,
            referrer_tg_id BIGINT NOT NULL,
            referred_tg_id BIGINT NOT NULL,
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            UNIQUE(shop_id, referred_tg_id)
        )
    """)
    op.execute("""
        CREATE TABLE IF NOT EXISTS plugin_pershop_buyers (
            shop_id BIGINT NOT NULL REFERENCES plugin_pershop_shops(id) ON DELETE CASCADE,
            tg_id BIGINT NOT NULL,
            lang TEXT NOT NULL DEFAULT 'en',
            scam_noted_at TIMESTAMPTZ,
            credit_units NUMERIC NOT NULL DEFAULT 0,
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            PRIMARY KEY (shop_id, tg_id)
        )
    """)
    op.execute("""
        CREATE TABLE IF NOT EXISTS plugin_pershop_scammers (
            tg_id BIGINT PRIMARY KEY,
            marked_by_admin TEXT NOT NULL DEFAULT '',
            reason TEXT NOT NULL DEFAULT '',
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
        )
    """)
    op.execute("""
        CREATE TABLE IF NOT EXISTS plugin_pershop_test_claims (
            tg_id BIGINT PRIMARY KEY,
            shop_id BIGINT NOT NULL REFERENCES plugin_pershop_shops(id) ON DELETE CASCADE,
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
        )
    """)
    op.execute("""
        CREATE TABLE IF NOT EXISTS plugin_pershop_broadcasts (
            id BIGSERIAL PRIMARY KEY,
            shop_id BIGINT NOT NULL REFERENCES plugin_pershop_shops(id) ON DELETE CASCADE,
            html_text TEXT NOT NULL DEFAULT '',
            photo BYTEA,
            recipients TEXT NOT NULL DEFAULT '[]',
            pos INT NOT NULL DEFAULT 0,
            total INT NOT NULL DEFAULT 0,
            done INT NOT NULL DEFAULT 0,
            failed INT NOT NULL DEFAULT 0,
            status TEXT NOT NULL DEFAULT 'queued',
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
        )
    """)
    op.execute("CREATE INDEX IF NOT EXISTS ix_shop_orders_shop_status ON plugin_pershop_orders(shop_id, status)")
    op.execute("CREATE INDEX IF NOT EXISTS ix_shop_subs_shop_buyer ON plugin_pershop_subs(shop_id, buyer_tg_id)")
    op.execute("CREATE INDEX IF NOT EXISTS ix_shop_subs_expire ON plugin_pershop_subs(expire_at)")
    op.execute("CREATE INDEX IF NOT EXISTS ix_shop_pkgs_shop_active ON plugin_pershop_packages(shop_id, active)")
    op.execute("CREATE INDEX IF NOT EXISTS ix_shop_tiers_shop ON plugin_pershop_tiers(shop_id)")
    op.execute("CREATE INDEX IF NOT EXISTS ix_shop_pkgs_tier ON plugin_pershop_packages(tier_id)")


def downgrade() -> None:
    from alembic import op

    for t in ("plugin_pershop_test_claims", "plugin_pershop_scammers", "plugin_pershop_broadcasts", "plugin_pershop_buyers", "plugin_pershop_referrals", "plugin_pershop_sessions", "plugin_pershop_subs",
              "plugin_pershop_orders", "plugin_pershop_services", "plugin_pershop_service_cats", "plugin_pershop_packages", "plugin_pershop_tiers",
              "plugin_pershop_shops"):
        op.execute(f"DROP TABLE IF EXISTS {t}")
