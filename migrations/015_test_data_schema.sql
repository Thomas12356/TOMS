-- Empty isolation boundary only. The maintenance command creates sample tables
-- from the models; it never copies real accounts, transactions or login records.
CREATE SCHEMA toms_demo;
REVOKE ALL ON SCHEMA toms_demo FROM PUBLIC;
GRANT USAGE ON SCHEMA toms_demo TO toms_app;
