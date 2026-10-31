INSERT INTO users VALUES
(1,'Zulu','gold',NULL),(2,'Zulu','silver','b@example.invalid'),
(3,'Alpha','silver',NULL),(4,'Alpha','gold','d@example.invalid');
INSERT INTO products VALUES
(8,'Second unknown','unknown',7,5),(5,'Near match','unknownish',999,1),
(6,'Missing category',NULL,13,0),(7,'Different case','Unknown',7,2),
(4,'First unknown','unknown',7,0);
INSERT INTO orders VALUES
(130,4,8,7,0,'completed','2026-01-15 00:00:00'),
(100,1,6,13,0,'pending','2025-12-31 23:59:59'),
(90,2,4,7,0,'completed','2026-02-01 00:00:00'),
(80,2,5,999,0,'cancelled','2026-01-31 00:00:00'),
(120,4,8,7,0,'completed','2026-02-01 00:00:00'),
(70,2,4,7,7,'completed','2026-01-01 00:00:00'),
(110,NULL,7,7,0,'completed','2026-01-31 23:59:59');
