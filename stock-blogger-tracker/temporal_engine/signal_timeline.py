"""
M1: 信号时间线 — 每天将 extract_signals() 输出追加到 SQLite
"""
from temporal_engine.db import get_conn


def ingest(signals, target_date=None):
    """
    将当日信号追加到 signal_timeline 表。
    
    Args:
        signals: List[Dict] (extract_signals 的输出格式)
        target_date: str 'YYYY-MM-DD', 默认今天
    
    Returns:
        dict: {'inserted': N, 'skipped': N, 'date': str}
    """
    from datetime import date as dt_date

    if not target_date:
        target_date = dt_date.today().isoformat()

    if not signals:
        return {'inserted': 0, 'skipped': 0, 'date': target_date, 'note': 'no signals'}

    conn = get_conn()
    inserted = 0
    skipped = 0

    for sig in signals:
        concept = sig.get('concept', '')
        author_id = sig.get('article_id', '')[:20] or 'unknown'
        source = sig.get('source', '')
        author = f"{source}:{author_id}"  # 简化的作者标识
        sentiment = sig.get('sentiment', 'neutral')
        direction = sig.get('direction', 'watch')
        confidence = sig.get('confidence', 0.5)

        if not concept:
            skipped += 1
            continue

        # 按 (date, concept, author_id) 去重，每天累加 article_count
        existing = conn.execute(
            "SELECT id, article_count FROM signal_timeline "
            "WHERE date=? AND concept=? AND author_id=?",
            (target_date, concept, author_id)
        ).fetchone()

        if existing:
            conn.execute(
                "UPDATE signal_timeline SET article_count = article_count + 1, "
                "confidence = MAX(confidence, ?) WHERE id = ?",
                (confidence, existing['id'])
            )
            skipped += 1
        else:
            conn.execute(
                "INSERT INTO signal_timeline (date, concept, source, author, author_id, "
                "sentiment, direction, confidence, article_count) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, 1)",
                (target_date, concept, source, author, author_id, sentiment, direction, confidence)
            )
            inserted += 1

    conn.commit()
    conn.close()

    return {
        'inserted': inserted,
        'skipped': skipped,
        'date': target_date,
    }


def query_date(date_str):
    """查询某日所有信号"""
    conn = get_conn()
    rows = conn.execute(
        "SELECT * FROM signal_timeline WHERE date=? ORDER BY concept, sentiment DESC",
        (date_str,)
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def query_concept(concept, days=30):
    """查询某概念最近N天的信号时间线"""
    conn = get_conn()
    rows = conn.execute(
        "SELECT * FROM signal_timeline WHERE concept=? AND date >= date('now', ?) "
        "ORDER BY date, sentiment DESC",
        (concept, f'-{days} days')
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]
