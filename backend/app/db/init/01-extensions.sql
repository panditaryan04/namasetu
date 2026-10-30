-- Runs once when the Postgres volume is first created. Migrations also ensure these exist.
CREATE EXTENSION IF NOT EXISTS pg_trgm;
CREATE EXTENSION IF NOT EXISTS unaccent;
