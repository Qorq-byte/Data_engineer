-- Test schema for NL2SQL agent
-- Simple e-commerce domain with users, orders, products

CREATE TABLE users (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    email TEXT NOT NULL UNIQUE,
    tier TEXT NOT NULL DEFAULT 'bronze',  -- bronze, silver, gold, platinum
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    last_active_at TEXT
);

CREATE TABLE products (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    category TEXT NOT NULL,
    price REAL NOT NULL,
    stock INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE orders (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL REFERENCES users(id),
    product_id INTEGER NOT NULL REFERENCES products(id),
    quantity INTEGER NOT NULL DEFAULT 1,
    amount REAL NOT NULL,        -- in yuan
    refund_amount REAL DEFAULT 0,
    status TEXT NOT NULL DEFAULT 'pending',  -- pending, confirmed, shipped, completed, cancelled
    region TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at TEXT
);

CREATE INDEX idx_orders_user_id ON orders(user_id);
CREATE INDEX idx_orders_status ON orders(status);
CREATE INDEX idx_orders_created_at ON orders(created_at);

-- Sample data
INSERT INTO users (id, name, email, tier, created_at) VALUES
    (1, 'Alice', 'alice@example.com', 'gold', '2026-01-15'),
    (2, 'Bob', 'bob@example.com', 'silver', '2026-02-20'),
    (3, 'Charlie', 'charlie@example.com', 'bronze', '2026-06-01');

INSERT INTO products (id, name, category, price, stock) VALUES
    (1, 'Laptop', 'Electronics', 5999.00, 50),
    (2, 'Mouse', 'Electronics', 129.00, 200),
    (3, 'Desk Chair', 'Furniture', 899.00, 30),
    (4, 'Coffee Mug', 'Kitchen', 49.00, 500);

INSERT INTO orders (id, user_id, product_id, quantity, amount, refund_amount, status, region, created_at) VALUES
    (1, 1, 1, 1, 5999.00, 0, 'completed', 'East', '2026-07-01'),
    (2, 1, 2, 2, 258.00, 0, 'completed', 'East', '2026-07-02'),
    (3, 2, 1, 1, 5999.00, 0, 'completed', 'North', '2026-07-03'),
    (4, 2, 3, 1, 899.00, 100.00, 'completed', 'North', '2026-07-05'),
    (5, 3, 2, 1, 129.00, 0, 'cancelled', 'West', '2026-07-10');
