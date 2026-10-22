-- Run as the migration owner after Alembic, in a dedicated Supabase project.
REVOKE CREATE ON SCHEMA public FROM PUBLIC;
REVOKE ALL ON ALL TABLES IN SCHEMA public FROM anon, authenticated, service_role;
REVOKE ALL ON ALL SEQUENCES IN SCHEMA public FROM anon, authenticated, service_role;
REVOKE EXECUTE ON ALL FUNCTIONS IN SCHEMA public FROM PUBLIC, anon, authenticated, service_role;
ALTER DEFAULT PRIVILEGES FOR ROLE postgres IN SCHEMA public REVOKE ALL ON TABLES FROM anon, authenticated, service_role;
ALTER DEFAULT PRIVILEGES FOR ROLE postgres IN SCHEMA public REVOKE ALL ON SEQUENCES FROM anon, authenticated, service_role;
ALTER DEFAULT PRIVILEGES FOR ROLE postgres IN SCHEMA public REVOKE EXECUTE ON FUNCTIONS FROM PUBLIC, anon, authenticated, service_role;
GRANT USAGE ON SCHEMA public TO aftercare_app;
GRANT EXECUTE ON FUNCTION public.prevent_audit_mutation() TO aftercare_app;
ALTER FUNCTION public.prevent_audit_mutation() SET search_path = pg_catalog;

DO $$
DECLARE table_name text;
BEGIN
    FOREACH table_name IN ARRAY ARRAY['tenants', 'auth_users', 'auth_memberships', 'auth_sessions', 'auth_tokens', 'auth_login_throttle', 'jobs', 'worker_heartbeats', 'model_budget'] LOOP
        EXECUTE format('ALTER TABLE public.%I ENABLE ROW LEVEL SECURITY', table_name);
        EXECUTE format('CREATE POLICY backend_only ON public.%I TO aftercare_app USING (true) WITH CHECK (true)', table_name);
    END LOOP;
END $$;
ALTER TABLE public.alembic_version ENABLE ROW LEVEL SECURITY;
