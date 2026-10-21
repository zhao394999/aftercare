GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public TO aftercare_app;
GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO aftercare_app;
REVOKE UPDATE, DELETE ON audit_events FROM aftercare_app;
REVOKE ALL ON alembic_version FROM aftercare_app;
