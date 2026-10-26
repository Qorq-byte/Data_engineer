-- Synthetic ecommerce v3; UTC TEXT timestamps, exact case-sensitive labels.
CREATE TABLE users (id INTEGER PRIMARY KEY, name TEXT NOT NULL, tier TEXT NOT NULL, email TEXT);
CREATE TABLE products (id INTEGER PRIMARY KEY, name TEXT NOT NULL, category TEXT,
                       price REAL NOT NULL, stock INTEGER NOT NULL);
CREATE TABLE orders (id INTEGER PRIMARY KEY, user_id INTEGER REFERENCES users(id),
                     product_id INTEGER NOT NULL REFERENCES products(id), amount REAL NOT NULL,
                     refund_amount REAL NOT NULL, status TEXT NOT NULL, created_at TEXT NOT NULL);
