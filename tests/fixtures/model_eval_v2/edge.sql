INSERT INTO users VALUES (1,'Zulu'), (2,'Zulu'), (3,'Alpha'), (4,'Alpha');
INSERT INTO products VALUES
    (8,'Second unknown','unknown'), (5,'Near match','unknownish'),
    (6,'Missing category',NULL), (7,'Different case','Unknown'), (4,'First unknown','unknown');
INSERT INTO orders VALUES
    (100,1,6,13,0,'pending'), (90,2,4,7,0,'completed'),
    (80,2,5,999,0,'cancelled'), (70,2,4,7,7,'completed');
