"""
M0: SQLite 数据库管理
"""
import sqlite3
from temporal_engine import DB_PATH

SCHEMA = """
CREATE TABLE IF NOT EXISTS signal_timeline (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    date TEXT NOT NULL,
    concept TEXT NOT NULL,
    source TEXT NOT NULL,
    author TEXT,
    author_id TEXT,
    sentiment TEXT NOT NULL CHECK(sentiment IN ('bullish','bearish','neutral')),
    direction TEXT,
    confidence REAL,
    article_count INTEGER DEFAULT 1,
    UNIQUE(date, concept, author_id)
);

CREATE INDEX IF NOT EXISTS idx_tl_date ON signal_timeline(date);
CREATE INDEX IF NOT EXISTS idx_tl_concept ON signal_timeline(concept);
CREATE INDEX IF NOT EXISTS idx_tl_date_concept ON signal_timeline(date, concept);
"""


def get_conn():
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.executescript(SCHEMA)
    return conn


def init_db():
    conn = get_conn()
    conn.close()
    print(f"[temporal] DB initialized at {DB_PATH}")
