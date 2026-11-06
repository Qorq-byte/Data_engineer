CREATE DATABASE de_stage67_fixture;
USE de_stage67_fixture;
CREATE TABLE orders (id INT NOT NULL, note VARCHAR(32))
DUPLICATE KEY(id) DISTRIBUTED BY HASH(id) BUCKETS 1 PROPERTIES ('replication_num'='1');
INSERT INTO orders VALUES (10,NULL),(20,'b'),(30,NULL),(40,'d'),(50,NULL);
CREATE TABLE long_rows (id BIGINT NOT NULL)
DUPLICATE KEY(id) DISTRIBUTED BY HASH(id) BUCKETS 1 PROPERTIES ('replication_num'='1');
INSERT INTO long_rows SELECT * FROM TABLE(generate_series(1,50000));
CREATE USER 'stage67_reader' IDENTIFIED BY 'stage67-disposable-reader';
GRANT SELECT ON ALL TABLES IN DATABASE de_stage67_fixture TO USER 'stage67_reader';
