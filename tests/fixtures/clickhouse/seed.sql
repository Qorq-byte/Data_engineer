CREATE DATABASE de_stage67_fixture;
CREATE TABLE de_stage67_fixture.orders (id UInt32, note Nullable(String)) ENGINE = Memory;
INSERT INTO de_stage67_fixture.orders VALUES (10,NULL),(20,'b'),(30,NULL),(40,'d'),(50,NULL);
CREATE TABLE de_stage67_fixture.long_rows (id UInt64) ENGINE = Memory;
INSERT INTO de_stage67_fixture.long_rows SELECT number FROM numbers(50000);
