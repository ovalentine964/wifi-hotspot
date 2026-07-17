-- ============================================================================
-- WiFi Hotspot Database Schema + Seed Data
-- PostgreSQL 15+
-- Matches Flask app models.py exactly
-- ============================================================================

-- ─── Plans ──────────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS plans (
    id              SERIAL PRIMARY KEY,
    name            VARCHAR(50) NOT NULL,
    price           NUMERIC(10, 2) NOT NULL,
    duration_hours  INTEGER NOT NULL,
    active          BOOLEAN DEFAULT TRUE
);

-- ─── Users ──────────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS users (
    id              SERIAL PRIMARY KEY,
    mac_address     VARCHAR(17) NOT NULL UNIQUE,
    phone_number    VARCHAR(15),
    plan_type       VARCHAR(10) DEFAULT 'paid',
    is_permanent    BOOLEAN DEFAULT FALSE,
    created_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- ─── Payments ───────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS payments (
    id              SERIAL PRIMARY KEY,
    mac_address     VARCHAR(17) NOT NULL,
    phone_number    VARCHAR(15) NOT NULL,
    amount          NUMERIC(10, 2) NOT NULL,
    plan_id         INTEGER REFERENCES plans(id),
    status          VARCHAR(20) DEFAULT 'pending',
    mpesa_code      VARCHAR(20),
    raw_sms         TEXT,
    created_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    confirmed_at    TIMESTAMP,
    expires_at      TIMESTAMP
);

-- ─── Sessions ───────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS sessions (
    id              SERIAL PRIMARY KEY,
    mac_address     VARCHAR(17) NOT NULL UNIQUE,
    phone_number    VARCHAR(15),
    plan_id         INTEGER REFERENCES plans(id),
    started_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    expires_at      TIMESTAMP NOT NULL,
    is_active       BOOLEAN DEFAULT TRUE
);

-- ─── Settings (key-value store) ─────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS settings (
    key             VARCHAR(50) PRIMARY KEY,
    value           TEXT NOT NULL
);

-- ─── Indexes ────────────────────────────────────────────────────────────────
CREATE INDEX IF NOT EXISTS idx_users_mac ON users(mac_address);
CREATE INDEX IF NOT EXISTS idx_users_phone ON users(phone_number);
CREATE INDEX IF NOT EXISTS idx_payments_mac ON payments(mac_address);
CREATE INDEX IF NOT EXISTS idx_payments_phone ON payments(phone_number);
CREATE INDEX IF NOT EXISTS idx_payments_status ON payments(status);
CREATE INDEX IF NOT EXISTS idx_payments_mpesa ON payments(mpesa_code);
CREATE INDEX IF NOT EXISTS idx_sessions_mac ON sessions(mac_address);
CREATE INDEX IF NOT EXISTS idx_sessions_active ON sessions(is_active, expires_at);
CREATE INDEX IF NOT EXISTS idx_sessions_expires ON sessions(expires_at);

-- ─── Seed Default Plans ─────────────────────────────────────────────────────
INSERT INTO plans (name, price, duration_hours, active) VALUES
    ('1 Hour',   20.00,  1,  TRUE),
    ('3 Hours',  50.00,  3,  TRUE),
    ('1 Day',   100.00, 24,  TRUE),
    ('1 Week',  400.00, 168, TRUE)
ON CONFLICT DO NOTHING;

-- ─── Seed Default Admin Credentials ─────────────────────────────────────────
-- Password: admin123 (bcrypt hash) — CHANGE THIS ON FIRST LOGIN
INSERT INTO settings (key, value) VALUES
    ('admin_user', 'admin'),
    ('admin_pass', '$2b$12$LJ3m4ys3Lk0TSwHjnF4oR.K3VJxqfVYqxSy3TqFG3YbP7b4sGuJ2O')
ON CONFLICT (key) DO NOTHING;

-- ─── Views ──────────────────────────────────────────────────────────────────

-- Active sessions with plan info
CREATE OR REPLACE VIEW v_active_sessions AS
SELECT
    s.id,
    s.mac_address,
    s.phone_number,
    s.plan_id,
    s.started_at,
    s.expires_at,
    p.name AS plan_name,
    p.price AS plan_price,
    p.duration_hours,
    EXTRACT(EPOCH FROM (s.expires_at - NOW()))::INTEGER AS seconds_remaining
FROM sessions s
JOIN plans p ON p.id = s.plan_id
WHERE s.is_active = TRUE AND s.expires_at > NOW();

-- Revenue summary
CREATE OR REPLACE VIEW v_revenue_daily AS
SELECT
    DATE(confirmed_at) AS day,
    COUNT(*) AS transactions,
    SUM(amount) AS total_revenue
FROM payments
WHERE status = 'confirmed'
GROUP BY DATE(confirmed_at)
ORDER BY day DESC;
