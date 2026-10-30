-- Least-privilege role for the API (run once as the RDS master user, after the first migration).
-- The API can read and write rows but owns nothing: it cannot alter tables, drop or disable the
-- guard triggers, or TRUNCATE. Append-only tables additionally reject UPDATE/DELETE by trigger.
--
--   psql "host=<database_address> dbname=acme user=acme_admin sslmode=require" \
--        -v app_password="'<db_app_password secret value>'" -f app_role.sql

CREATE ROLE acme_app LOGIN PASSWORD :app_password NOSUPERUSER NOCREATEDB NOCREATEROLE;

GRANT CONNECT ON DATABASE acme TO acme_app;
GRANT USAGE ON SCHEMA public TO acme_app;

GRANT SELECT, INSERT, UPDATE ON ALL TABLES IN SCHEMA public TO acme_app;
GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO acme_app;

-- Tables created by later migrations get the same grants.
ALTER DEFAULT PRIVILEGES FOR ROLE acme_admin IN SCHEMA public
    GRANT SELECT, INSERT, UPDATE ON TABLES TO acme_app;
ALTER DEFAULT PRIVILEGES FOR ROLE acme_admin IN SCHEMA public
    GRANT USAGE, SELECT ON SEQUENCES TO acme_app;

-- No DELETE or TRUNCATE is granted: nothing in the domain deletes rows.
