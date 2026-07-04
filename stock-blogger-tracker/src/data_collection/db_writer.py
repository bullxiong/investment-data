"""
SQLite写入器 — 3AI协作数据交换基础
管理 articles + text_signals 两张表
"""
import sqlite3, json, os

PROJECT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
DB_PATH = os.path.join(PROJECT, 'data', 'collaboration.db')
DB_PATH = os.path.join(PROJECT, 'data', 'collaboration.db')


def init_db(db_path=None):
    """初始化articles + text_signals表"""
    path = db_path or DB_PATH
    conn = sqlite3.connect(path)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS articles (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            source TEXT NOT NULL,
            url TEXT UNIQUE,
            title TEXT,
            author TEXT,
            author_id TEXT,
            content TEXT,
            summary TEXT,
            tags TEXT,
            created_at TEXT,
            fetched_at TEXT DEFAULT (datetime('now', 'localtime')),
            status TEXT DEFAULT 'fetched'
        );

        CREATE TABLE IF NOT EXISTS text_signals (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            article_id INTEGER,
            source TEXT,
            concept TEXT,
            related_stocks TEXT,
            sentiment TEXT,
            direction TEXT,
            confidence REAL,
            rationale TEXT,
            extracted_at TEXT DEFAULT (datetime('now', 'localtime')),
            FOREIGN KEY (article_id) REFERENCES articles(id)
        );

        CREATE INDEX IF NOT EXISTS idx_articles_url ON articles(url);
        CREATE INDEX IF NOT EXISTS idx_signals_concept ON text_signals(concept);
        CREATE INDEX IF NOT EXISTS idx_signals_sentiment ON text_signals(sentiment);
    """)
    conn.commit()
    return conn


def insert_articles(conn, articles):
    """INSERT或IGNORE文章（按url去重）"""
    count = 0
    for a in articles:
        try:
            conn.execute("""
                INSERT OR IGNORE INTO articles 
                (source, url, title, author, author_id, content, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)
            """, (
                a.get('source', ''),
                a.get('url', ''),
                a.get('title', ''),
                a.get('author', ''),
                a.get('author_id', ''),
                a.get('content', ''),
                a.get('created_at', ''),
            ))
            if conn.changes:
                count += 1
        except Exception:
            continue
    conn.commit()
    return count


def insert_signals(conn, signals):
    """INSERT信号"""
    count = 0
    for s in signals:
        try:
            # Find article_id by URL or article_id
            aid = s.get('article_id', '')
            if not aid:
                continue
            
            # Try to find matching article in DB
            row = conn.execute(
                "SELECT id FROM articles WHERE url LIKE ? OR author_id = ? LIMIT 1",
                (f'%{aid}%', aid)
            ).fetchone()
            db_article_id = row[0] if row else None
            
            conn.execute("""
                INSERT INTO text_signals 
                (article_id, source, concept, related_stocks, sentiment, 
                 direction, confidence, rationale)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                db_article_id,
                s.get('source', ''),
                s.get('concept', ''),
                s.get('related_stocks', ''),
                s.get('sentiment', ''),
                s.get('direction', ''),
                s.get('confidence', 0.5),
                s.get('rationale', ''),
            ))
            count += 1
        except Exception:
            continue
    conn.commit()
    return count


if __name__ == '__main__':
    conn = init_db()
    print(f"DB: {DB_PATH}")
    print(f"articles: {conn.execute('SELECT COUNT(*) FROM articles').fetchone()[0]}")
    print(f"signals: {conn.execute('SELECT COUNT(*) FROM text_signals').fetchone()[0]}")
    conn.close()
