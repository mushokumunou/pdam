-- Integrity checks that Directus field metadata could not express reliably.
--
-- Background: setting field meta `validation` (e.g. {"_regex": "..."}) on Directus 12.6.1 makes
-- every item create/update fail with `500 Maximum call stack size exceeded` (lodash merge
-- overflow inside the items service). PostgreSQL CHECK constraints give the same guarantee,
-- enforced at the database, and are idempotent to re-apply.
--
-- Apply: psql -f schema/checks.sql  (safe to re-run)

BEGIN;

ALTER TABLE vps DROP CONSTRAINT IF EXISTS vps_ipv4_chk;
ALTER TABLE vps ADD CONSTRAINT vps_ipv4_chk CHECK (
  ipv4 IS NULL OR ipv4 ~ '^((25[0-5]|2[0-4][0-9]|1?[0-9]?[0-9])\.){3}(25[0-5]|2[0-4][0-9]|1?[0-9]?[0-9])$');

ALTER TABLE vps DROP CONSTRAINT IF EXISTS vps_ipv6_chk;
ALTER TABLE vps ADD CONSTRAINT vps_ipv6_chk CHECK (
  ipv6 IS NULL OR ipv6 ~ '^[0-9A-Fa-f:]+$');

ALTER TABLE payment_methods DROP CONSTRAINT IF EXISTS pm_last_four_chk;
ALTER TABLE payment_methods ADD CONSTRAINT pm_last_four_chk CHECK (
  last_four IS NULL OR last_four ~ '^[0-9]{4}$');

ALTER TABLE payment_methods DROP CONSTRAINT IF EXISTS pm_currency_chk;
ALTER TABLE payment_methods ADD CONSTRAINT pm_currency_chk CHECK (
  currency IS NULL OR currency ~ '^[A-Z]{3}$');

ALTER TABLE subscriptions DROP CONSTRAINT IF EXISTS sub_currency_chk;
ALTER TABLE subscriptions ADD CONSTRAINT sub_currency_chk CHECK (
  currency IS NULL OR currency ~ '^[A-Z]{3}$');

ALTER TABLE subscriptions DROP CONSTRAINT IF EXISTS sub_price_chk;
ALTER TABLE subscriptions ADD CONSTRAINT sub_price_chk CHECK (price IS NULL OR price >= 0);

-- FQDNs are case-insensitive: keep the stored form canonical instead of relying on the client.
ALTER TABLE domains DROP CONSTRAINT IF EXISTS domains_lower_chk;
ALTER TABLE domains ADD CONSTRAINT domains_lower_chk CHECK (name = lower(name));

COMMIT;
