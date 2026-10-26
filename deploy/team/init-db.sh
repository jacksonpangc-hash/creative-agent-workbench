#!/bin/sh
set -eu
runtime_password=$(cat /run/secrets/runtime_password)
psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname "$POSTGRES_DB" --set=runtime_password="$runtime_password" <<'SQL'
CREATE ROLE creative_runtime LOGIN NOSUPERUSER NOBYPASSRLS PASSWORD :'runtime_password';
REVOKE CREATE ON SCHEMA public FROM PUBLIC;
SQL
