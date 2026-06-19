DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'awcp_app') THEN
        CREATE ROLE awcp_app LOGIN PASSWORD 'awcp_app_password';
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'awcp_ro') THEN
        CREATE ROLE awcp_ro LOGIN PASSWORD 'awcp_ro_password';
    END IF;
END
$$;
