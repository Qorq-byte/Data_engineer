INSERT INTO users VALUES
(1,'Ada','gold','ada@example.invalid'),(2,'Bo','silver','bo@example.invalid'),
(3,'Chen','bronze',NULL),(4,'Dia','silver','dia@example.invalid');
INSERT INTO products VALUES
(1,'Notebook','books',100,5),(2,'Pen','stationery',20,0),
(3,'Tea','grocery',7,10),(4,'Mystery',NULL,5,2);
INSERT INTO orders VALUES
(60,NULL,4,5,0,'completed','2026-01-15 12:00:00'),
(30,2,1,40,0,'cancelled','2026-02-01 00:00:00'),
(50,3,3,7,7,'completed','2026-02-01 00:00:00'),
(10,1,1,100,15,'completed','2026-01-01 00:00:00'),
(40,2,3,7,0,'pending','2025-12-31 23:59:59'),
(20,1,2,20,0,'completed','2026-01-31 23:59:59');
