"""Create a test DB with multiple tables containing a 'name' column."""
import sqlite3
from pathlib import Path

DB_DIR = Path("data/test_dbs")
DB_DIR.mkdir(parents=True, exist_ok=True)

conn = sqlite3.connect(DB_DIR / "multi_table.db")
cur = conn.cursor()
cur.executescript("""
    CREATE TABLE IF NOT EXISTS users (
        id INTEGER PRIMARY KEY,
        name TEXT NOT NULL,
        email TEXT
    );
    CREATE TABLE IF NOT EXISTS employees (
        id INTEGER PRIMARY KEY,
        name TEXT NOT NULL,
        department TEXT,
        salary REAL
    );
    CREATE TABLE IF NOT EXISTS customers (
        id INTEGER PRIMARY KEY,
        name TEXT NOT NULL,
        phone TEXT,
        address TEXT
    );
    INSERT OR REPLACE INTO users VALUES (1, 'Alice', 'alice@example.com');
    INSERT OR REPLACE INTO users VALUES (2, 'Bob', 'bob@example.com');
    INSERT OR REPLACE INTO employees VALUES (1, 'Charlie', 'Engineering', 80000);
    INSERT OR REPLACE INTO employees VALUES (2, 'Diana', 'Sales', 60000);
    INSERT OR REPLACE INTO customers VALUES (1, 'Eve', '555-1234', '123 Main St');
    INSERT OR REPLACE INTO customers VALUES (2, 'Frank', '555-5678', '456 Oak Ave');
""")
conn.commit()
conn.close()
print(f"Created {DB_DIR / 'multi_table.db'} (users, employees, customers — all have 'name' column)")
