-- Independent synthetic profile. Never change the v1 shared fixture.
CREATE TABLE users (id INTEGER PRIMARY KEY, name TEXT NOT NULL);
CREATE TABLE products (id INTEGER PRIMARY KEY, name TEXT NOT NULL, category TEXT);
CREATE TABLE orders (
    id INTEGER PRIMARY KEY,
    user_id INTEGER NOT NULL REFERENCES users(id),
    product_id INTEGER NOT NULL REFERENCES products(id),
    amount REAL NOT NULL,
    refund_amount REAL NOT NULL DEFAULT 0,
    status TEXT NOT NULL
);
