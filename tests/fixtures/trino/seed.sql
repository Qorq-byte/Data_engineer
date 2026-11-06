CREATE SCHEMA fixture.acceptance;
CREATE TABLE fixture.acceptance.orders (id integer, note varchar);
INSERT INTO fixture.acceptance.orders VALUES (10,NULL),(20,'b'),(30,NULL),(40,'d'),(50,NULL);
CREATE TABLE fixture.acceptance.long_rows AS
SELECT a*1000+b AS id
FROM UNNEST(sequence(1,100)) AS t(a) CROSS JOIN UNNEST(sequence(1,1000)) AS u(b);
