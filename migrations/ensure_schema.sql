-- =============================================================================
-- saas_platform: idempotent schema patch
-- =============================================================================
-- Brings an existing Railway PostgreSQL database in line with app/models.py.
-- Safe to run multiple times. Does NOT drop tables, columns or data, and does
-- NOT rename anything. Only:
--   * CREATE TABLE IF NOT EXISTS  (covers fresh DB / missing tables)
--   * ALTER TABLE ... ADD COLUMN IF NOT EXISTS
--   * CREATE INDEX IF NOT EXISTS
--   * adds FKs only when missing (via DO block + pg_constraint)
-- =============================================================================

BEGIN;

-- ---------------------------------------------------------------------------
-- 1. plans
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS plans (
    id              SERIAL PRIMARY KEY,
    name            VARCHAR(128) NOT NULL UNIQUE,
    price_monthly   NUMERIC(10, 2) NOT NULL DEFAULT 0,
    can_buyout      BOOLEAN NOT NULL DEFAULT FALSE,
    buyout_months   INTEGER
);

ALTER TABLE plans ADD COLUMN IF NOT EXISTS slug                     VARCHAR(64);
ALTER TABLE plans ADD COLUMN IF NOT EXISTS price                    NUMERIC(10, 2);
ALTER TABLE plans ADD COLUMN IF NOT EXISTS currency                 VARCHAR(8)  NOT NULL DEFAULT 'USD';
ALTER TABLE plans ADD COLUMN IF NOT EXISTS products_limit           INTEGER;
ALTER TABLE plans ADD COLUMN IF NOT EXISTS images_per_product_limit INTEGER;
ALTER TABLE plans ADD COLUMN IF NOT EXISTS domains_limit            INTEGER;
ALTER TABLE plans ADD COLUMN IF NOT EXISTS users_limit              INTEGER;
ALTER TABLE plans ADD COLUMN IF NOT EXISTS analytics_enabled        BOOLEAN NOT NULL DEFAULT FALSE;
ALTER TABLE plans ADD COLUMN IF NOT EXISTS active                   BOOLEAN NOT NULL DEFAULT TRUE;
ALTER TABLE plans ADD COLUMN IF NOT EXISTS created_at               TIMESTAMPTZ DEFAULT NOW();
-- Backfill new "price" column from legacy "price_monthly"
UPDATE plans SET price = price_monthly WHERE price IS NULL;

CREATE UNIQUE INDEX IF NOT EXISTS uq_plans_slug ON plans (slug);
CREATE INDEX        IF NOT EXISTS ix_plans_slug ON plans (slug);

-- Plan price bumps (idempotent, value-guarded so we don't overwrite custom prices).
-- Starter: $10 -> $15
UPDATE plans
   SET price = 15,
       price_monthly = 15
 WHERE (slug = 'starter' OR LOWER(name) LIKE 'starter%')
   AND COALESCE(price, price_monthly, 0) = 10;


-- ---------------------------------------------------------------------------
-- 2. clients
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS clients (
    id                   SERIAL PRIMARY KEY,
    business_name        VARCHAR(255) NOT NULL,
    slug                 VARCHAR(64)  NOT NULL UNIQUE,
    telegram_bot_token   VARCHAR(255),
    admin_telegram_id    BIGINT,
    status               VARCHAR(32)  NOT NULL DEFAULT 'active',
    created_at           TIMESTAMPTZ  NOT NULL DEFAULT NOW()
);

ALTER TABLE clients ADD COLUMN IF NOT EXISTS template_name  VARCHAR(64) NOT NULL DEFAULT 'technovlada';
ALTER TABLE clients ADD COLUMN IF NOT EXISTS domain_status  VARCHAR(32) NOT NULL DEFAULT 'pending';
ALTER TABLE clients ADD COLUMN IF NOT EXISTS plan_id        INTEGER;
ALTER TABLE clients ADD COLUMN IF NOT EXISTS bot_username   VARCHAR(64);
ALTER TABLE clients ADD COLUMN IF NOT EXISTS bot_id         BIGINT;
ALTER TABLE clients ADD COLUMN IF NOT EXISTS bot_admin_ids  TEXT;
ALTER TABLE clients ADD COLUMN IF NOT EXISTS dashboard_token VARCHAR(64);
ALTER TABLE clients ADD COLUMN IF NOT EXISTS bot_mode       VARCHAR(16) NOT NULL DEFAULT 'shared';
-- Backfill tokens for existing clients
UPDATE clients
   SET dashboard_token = md5(random()::text || id::text || clock_timestamp()::text)
 WHERE dashboard_token IS NULL;

CREATE INDEX IF NOT EXISTS ix_clients_slug    ON clients (slug);
CREATE INDEX IF NOT EXISTS ix_clients_plan_id ON clients (plan_id);

-- FK clients.plan_id -> plans.id (idempotent)
DO $$ BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint WHERE conname = 'fk_clients_plan_id'
    ) THEN
        ALTER TABLE clients
            ADD CONSTRAINT fk_clients_plan_id
            FOREIGN KEY (plan_id) REFERENCES plans(id) ON DELETE SET NULL;
    END IF;
END $$;


-- ---------------------------------------------------------------------------
-- 3. subscriptions
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS subscriptions (
    id          SERIAL PRIMARY KEY,
    client_id   INTEGER NOT NULL REFERENCES clients(id) ON DELETE CASCADE,
    plan_id     INTEGER NOT NULL REFERENCES plans(id)   ON DELETE RESTRICT,
    status      VARCHAR(32) NOT NULL DEFAULT 'active',
    expires_at  TIMESTAMPTZ
);

ALTER TABLE subscriptions ADD COLUMN IF NOT EXISTS status     VARCHAR(32) NOT NULL DEFAULT 'active';
ALTER TABLE subscriptions ADD COLUMN IF NOT EXISTS starts_at  TIMESTAMPTZ;
ALTER TABLE subscriptions ADD COLUMN IF NOT EXISTS expires_at TIMESTAMPTZ;

CREATE INDEX IF NOT EXISTS ix_subscriptions_client_id ON subscriptions (client_id);
CREATE INDEX IF NOT EXISTS ix_subscriptions_plan_id   ON subscriptions (plan_id);


-- ---------------------------------------------------------------------------
-- 4. site_requests
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS site_requests (
    id            SERIAL PRIMARY KEY,
    business_name VARCHAR(255) NOT NULL,
    telegram      VARCHAR(128) NOT NULL,
    site_type     VARCHAR(64)  NOT NULL,
    plan          VARCHAR(64)  NOT NULL,
    comment       TEXT,
    status        VARCHAR(32)  NOT NULL DEFAULT 'new',
    created_at    TIMESTAMPTZ  NOT NULL DEFAULT NOW()
);

ALTER TABLE site_requests ADD COLUMN IF NOT EXISTS comment    TEXT;
ALTER TABLE site_requests ADD COLUMN IF NOT EXISTS status     VARCHAR(32) NOT NULL DEFAULT 'new';
ALTER TABLE site_requests ADD COLUMN IF NOT EXISTS created_at TIMESTAMPTZ NOT NULL DEFAULT NOW();


-- ---------------------------------------------------------------------------
-- 5. products
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS products (
    id           SERIAL PRIMARY KEY,
    client_id    INTEGER NOT NULL REFERENCES clients(id) ON DELETE CASCADE,
    name         VARCHAR(255) NOT NULL,
    price        NUMERIC(10, 2) NOT NULL DEFAULT 0,
    created_at   TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

ALTER TABLE products ADD COLUMN IF NOT EXISTS category     VARCHAR(128);
ALTER TABLE products ADD COLUMN IF NOT EXISTS description  TEXT;
ALTER TABLE products ADD COLUMN IF NOT EXISTS image_url    VARCHAR(1024);
ALTER TABLE products ADD COLUMN IF NOT EXISTS brand        VARCHAR(128);
ALTER TABLE products ADD COLUMN IF NOT EXISTS old_price    NUMERIC(10, 2);
ALTER TABLE products ADD COLUMN IF NOT EXISTS specs        TEXT;
ALTER TABLE products ADD COLUMN IF NOT EXISTS is_available BOOLEAN NOT NULL DEFAULT TRUE;
ALTER TABLE products ADD COLUMN IF NOT EXISTS created_at   TIMESTAMPTZ NOT NULL DEFAULT NOW();
ALTER TABLE products ADD COLUMN IF NOT EXISTS group_name   VARCHAR(128);
ALTER TABLE products ADD COLUMN IF NOT EXISTS badge            VARCHAR(64);
ALTER TABLE products ADD COLUMN IF NOT EXISTS seo_title        VARCHAR(255);
ALTER TABLE products ADD COLUMN IF NOT EXISTS seo_description  TEXT;
ALTER TABLE products ADD COLUMN IF NOT EXISTS seo_keywords     TEXT;

CREATE INDEX IF NOT EXISTS ix_products_client_id ON products (client_id);


-- ---------------------------------------------------------------------------
-- 6. payment_requests (legacy webhook table)
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS payment_requests (
    id          SERIAL PRIMARY KEY,
    client_slug VARCHAR(64) NOT NULL,
    type        VARCHAR(32) NOT NULL,
    amount      NUMERIC(10, 2),
    currency    VARCHAR(8),
    external_id VARCHAR(128),
    note        TEXT,
    status      VARCHAR(32) NOT NULL DEFAULT 'pending',
    created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

ALTER TABLE payment_requests ADD COLUMN IF NOT EXISTS amount      NUMERIC(10, 2);
ALTER TABLE payment_requests ADD COLUMN IF NOT EXISTS currency    VARCHAR(8);
ALTER TABLE payment_requests ADD COLUMN IF NOT EXISTS external_id VARCHAR(128);
ALTER TABLE payment_requests ADD COLUMN IF NOT EXISTS note        TEXT;
ALTER TABLE payment_requests ADD COLUMN IF NOT EXISTS status      VARCHAR(32) NOT NULL DEFAULT 'pending';
ALTER TABLE payment_requests ADD COLUMN IF NOT EXISTS created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW();

CREATE INDEX IF NOT EXISTS ix_payment_requests_client_slug ON payment_requests (client_slug);


-- ---------------------------------------------------------------------------
-- 7. payments
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS payments (
    id              SERIAL PRIMARY KEY,
    client_id       INTEGER NOT NULL REFERENCES clients(id) ON DELETE CASCADE,
    subscription_id INTEGER          REFERENCES subscriptions(id) ON DELETE SET NULL,
    payment_type    VARCHAR(32)  NOT NULL,
    provider        VARCHAR(32)  NOT NULL DEFAULT 'manual',
    amount          NUMERIC(10, 2) NOT NULL,
    currency        VARCHAR(8)   NOT NULL DEFAULT 'USD',
    status          VARCHAR(16)  NOT NULL DEFAULT 'pending',
    invoice_id      VARCHAR(128),
    payment_url     VARCHAR(1024),
    created_at      TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    paid_at         TIMESTAMPTZ
);

ALTER TABLE payments ADD COLUMN IF NOT EXISTS subscription_id INTEGER;
ALTER TABLE payments ADD COLUMN IF NOT EXISTS provider        VARCHAR(32)  NOT NULL DEFAULT 'manual';
ALTER TABLE payments ADD COLUMN IF NOT EXISTS currency        VARCHAR(8)   NOT NULL DEFAULT 'USD';
ALTER TABLE payments ADD COLUMN IF NOT EXISTS status          VARCHAR(16)  NOT NULL DEFAULT 'pending';
ALTER TABLE payments ADD COLUMN IF NOT EXISTS invoice_id      VARCHAR(128);
ALTER TABLE payments ADD COLUMN IF NOT EXISTS payment_url     VARCHAR(1024);
ALTER TABLE payments ADD COLUMN IF NOT EXISTS created_at      TIMESTAMPTZ  NOT NULL DEFAULT NOW();
ALTER TABLE payments ADD COLUMN IF NOT EXISTS paid_at         TIMESTAMPTZ;

DO $$ BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint WHERE conname = 'fk_payments_subscription_id'
    ) THEN
        ALTER TABLE payments
            ADD CONSTRAINT fk_payments_subscription_id
            FOREIGN KEY (subscription_id) REFERENCES subscriptions(id) ON DELETE SET NULL;
    END IF;
END $$;

CREATE INDEX IF NOT EXISTS ix_payments_client_id       ON payments (client_id);
CREATE INDEX IF NOT EXISTS ix_payments_subscription_id ON payments (subscription_id);
CREATE INDEX IF NOT EXISTS ix_payments_invoice_id      ON payments (invoice_id);


-- ---------------------------------------------------------------------------
-- 8. domains
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS domains (
    id            SERIAL PRIMARY KEY,
    client_id     INTEGER NOT NULL REFERENCES clients(id) ON DELETE CASCADE,
    domain        VARCHAR(255) UNIQUE,
    status        VARCHAR(32)  NOT NULL DEFAULT 'pending',
    expires_at    TIMESTAMPTZ,
    dns_connected BOOLEAN      NOT NULL DEFAULT FALSE,
    created_at    TIMESTAMPTZ  NOT NULL DEFAULT NOW()
);

ALTER TABLE domains ADD COLUMN IF NOT EXISTS status        VARCHAR(32) NOT NULL DEFAULT 'pending';
ALTER TABLE domains ADD COLUMN IF NOT EXISTS expires_at    TIMESTAMPTZ;
ALTER TABLE domains ADD COLUMN IF NOT EXISTS dns_connected BOOLEAN     NOT NULL DEFAULT FALSE;
ALTER TABLE domains ADD COLUMN IF NOT EXISTS created_at    TIMESTAMPTZ NOT NULL DEFAULT NOW();
-- Allow NULL domain (placeholder for "not connected yet")
ALTER TABLE domains ALTER COLUMN domain DROP NOT NULL;

CREATE INDEX IF NOT EXISTS ix_domains_client_id ON domains (client_id);
CREATE INDEX IF NOT EXISTS ix_domains_domain    ON domains (domain);


-- ---------------------------------------------------------------------------
-- 9. client_settings (onboarding defaults)
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS client_settings (
    client_id  INTEGER PRIMARY KEY REFERENCES clients(id) ON DELETE CASCADE,
    language   VARCHAR(8)  NOT NULL DEFAULT 'uk',
    currency   VARCHAR(8)  NOT NULL DEFAULT 'UAH',
    timezone   VARCHAR(64) NOT NULL DEFAULT 'Europe/Kyiv',
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

ALTER TABLE client_settings ADD COLUMN IF NOT EXISTS language   VARCHAR(8)  NOT NULL DEFAULT 'uk';
ALTER TABLE client_settings ADD COLUMN IF NOT EXISTS currency   VARCHAR(8)  NOT NULL DEFAULT 'UAH';
ALTER TABLE client_settings ADD COLUMN IF NOT EXISTS timezone   VARCHAR(64) NOT NULL DEFAULT 'Europe/Kyiv';
ALTER TABLE client_settings ADD COLUMN IF NOT EXISTS theme_name    VARCHAR(64)   NOT NULL DEFAULT 'light_red';
ALTER TABLE client_settings ADD COLUMN IF NOT EXISTS shop_title    VARCHAR(255);
ALTER TABLE client_settings ADD COLUMN IF NOT EXISTS phone         VARCHAR(64);
ALTER TABLE client_settings ADD COLUMN IF NOT EXISTS address       VARCHAR(255);
ALTER TABLE client_settings ADD COLUMN IF NOT EXISTS telegram_url  VARCHAR(512);
ALTER TABLE client_settings ADD COLUMN IF NOT EXISTS instagram_url VARCHAR(512);
ALTER TABLE client_settings ADD COLUMN IF NOT EXISTS logo_url      VARCHAR(1024);
ALTER TABLE client_settings ADD COLUMN IF NOT EXISTS created_at TIMESTAMPTZ NOT NULL DEFAULT NOW();


-- ---------------------------------------------------------------------------
-- 10. orders
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS orders (
    id             SERIAL PRIMARY KEY,
    client_id      INTEGER        NOT NULL REFERENCES clients(id) ON DELETE CASCADE,
    customer_name  VARCHAR(255)   NOT NULL,
    customer_phone VARCHAR(64)    NOT NULL,
    customer_city  VARCHAR(255),
    comment        TEXT,
    items_json     TEXT           NOT NULL DEFAULT '[]',
    total          NUMERIC(10, 2) NOT NULL DEFAULT 0,
    status         VARCHAR(32)    NOT NULL DEFAULT 'new',
    created_at     TIMESTAMPTZ    NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS ix_orders_client_id ON orders (client_id);
CREATE INDEX IF NOT EXISTS ix_orders_status    ON orders (status);


-- ---------------------------------------------------------------------------
-- 11. billing_state
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS billing_state (
    client_id        INTEGER PRIMARY KEY REFERENCES clients(id) ON DELETE CASCADE,
    status           VARCHAR(32) NOT NULL DEFAULT 'active',
    trial_days_left  INTEGER     NOT NULL DEFAULT 0,
    updated_at       TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

ALTER TABLE billing_state ADD COLUMN IF NOT EXISTS status          VARCHAR(32) NOT NULL DEFAULT 'active';
ALTER TABLE billing_state ADD COLUMN IF NOT EXISTS trial_days_left INTEGER     NOT NULL DEFAULT 0;
ALTER TABLE billing_state ADD COLUMN IF NOT EXISTS updated_at      TIMESTAMPTZ NOT NULL DEFAULT NOW();


-- ---------------------------------------------------------------------------
-- 12. limits_snapshots
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS limits_snapshots (
    id                       SERIAL PRIMARY KEY,
    client_id                INTEGER NOT NULL REFERENCES clients(id) ON DELETE CASCADE,
    plan_id                  INTEGER          REFERENCES plans(id)   ON DELETE SET NULL,
    products_limit           INTEGER,
    images_per_product_limit INTEGER,
    domains_limit            INTEGER,
    users_limit              INTEGER,
    created_at               TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

ALTER TABLE limits_snapshots ADD COLUMN IF NOT EXISTS plan_id                  INTEGER;
ALTER TABLE limits_snapshots ADD COLUMN IF NOT EXISTS products_limit           INTEGER;
ALTER TABLE limits_snapshots ADD COLUMN IF NOT EXISTS images_per_product_limit INTEGER;
ALTER TABLE limits_snapshots ADD COLUMN IF NOT EXISTS domains_limit            INTEGER;
ALTER TABLE limits_snapshots ADD COLUMN IF NOT EXISTS users_limit              INTEGER;
ALTER TABLE limits_snapshots ADD COLUMN IF NOT EXISTS created_at               TIMESTAMPTZ NOT NULL DEFAULT NOW();

CREATE INDEX IF NOT EXISTS ix_limits_snapshots_client_id ON limits_snapshots (client_id);

DO $$ BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint WHERE conname = 'fk_limits_snapshots_plan_id'
    ) THEN
        ALTER TABLE limits_snapshots
            ADD CONSTRAINT fk_limits_snapshots_plan_id
            FOREIGN KEY (plan_id) REFERENCES plans(id) ON DELETE SET NULL;
    END IF;
END $$;

COMMIT;

-- ---------------------------------------------------------------------------
-- site_events
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS site_events (
    id          SERIAL PRIMARY KEY,
    client_id   INTEGER NOT NULL REFERENCES clients(id) ON DELETE CASCADE,
    event_type  VARCHAR(32) NOT NULL,
    product_id  INTEGER,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS ix_site_events_client_id  ON site_events (client_id);
CREATE INDEX IF NOT EXISTS ix_site_events_created_at ON site_events (created_at);

-- ---------------------------------------------------------------------------
-- 14. product_specs  (structured per-product spec rows for sidebar filters)
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS product_specs (
    id          SERIAL PRIMARY KEY,
    product_id  INTEGER NOT NULL REFERENCES products(id) ON DELETE CASCADE,
    client_id   INTEGER NOT NULL REFERENCES clients(id)  ON DELETE CASCADE,
    name        VARCHAR(128) NOT NULL,
    value       VARCHAR(512) NOT NULL
);

CREATE INDEX IF NOT EXISTS ix_product_specs_product_id ON product_specs (product_id);
CREATE INDEX IF NOT EXISTS ix_product_specs_client_id  ON product_specs (client_id);

-- ---------------------------------------------------------------------------
-- 15. category_specs  (filterable spec metadata per category)
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS category_specs (
    id             SERIAL PRIMARY KEY,
    client_id      INTEGER NOT NULL REFERENCES clients(id) ON DELETE CASCADE,
    category       VARCHAR(128) NOT NULL,
    name           VARCHAR(128) NOT NULL,
    is_filterable  BOOLEAN NOT NULL DEFAULT TRUE
);

CREATE INDEX IF NOT EXISTS ix_category_specs_client_id ON category_specs (client_id);

-- =============================================================================
-- HOW TO RUN ON RAILWAY
-- =============================================================================
-- 1. Open the Railway project → PostgreSQL plugin → "Data" → "Query" tab.
-- 2. Paste the entire content of this file into the query editor.
-- 3. Click "Run" (Cmd/Ctrl+Enter). The script is idempotent — re-running it
--    is safe and will report "0 rows affected" once schema is in sync.
-- 4. Alternatively, from a local shell:
--      psql "$DATABASE_URL" -f migrations/ensure_schema.sql
--    where $DATABASE_URL is the Railway PostgreSQL connection string
--    (Settings → Variables → DATABASE_URL).
-- =============================================================================
