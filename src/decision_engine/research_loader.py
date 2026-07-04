"""
research_loader.py - 从 SQLite 加载研报数据 → 决策引擎输入

为 industry/company/valuation agent 提供真实数据。
"""

from __future__ import annotations
import json
import sqlite3
from pathlib import Path
from typing import Any, Dict, List, Optional


DB_PATH = Path("data/stock_pool.db")


def get_db() -> sqlite3.Connection:
    """获取数据库连接"""
    if not DB_PATH.exists():
        raise FileNotFoundError(f"研报库不存在: {DB_PATH}")
    return sqlite3.connect(DB_PATH)


# ============ 查询函数 ============
def get_research_by_stock(stock_code: str, limit: int = 20) -> List[Dict[str, Any]]:
    """获取某只股票的所有研报"""
    conn = get_db()
    try:
        cur = conn.cursor()
        cur.execute("""
            SELECT id, topic_id, stock_code, stock_name, title, content, author,
                   publish_time, source_type, source_url, rating, target_price,
                   sentiment_score, key_concepts
            FROM research_records
            WHERE stock_code = ?
            ORDER BY publish_time DESC
            LIMIT ?
        """, (stock_code, limit))
        cols = [d[0] for d in cur.description]
        rows = cur.fetchall()
    finally:
        conn.close()
    return [dict(zip(cols, row)) for row in rows]


def get_stocks_with_research(min_count: int = 1) -> List[Dict[str, Any]]:
    """获取有研报的股票列表"""
    conn = get_db()
    try:
        cur = conn.cursor()
        cur.execute("""
            SELECT s.code, s.name, s.market, s.industry, s.sector, s.description,
                   COUNT(r.id) as research_count,
                   MAX(r.publish_time) as latest_research
            FROM stocks s
            INNER JOIN research_records r ON s.code = r.stock_code
            GROUP BY s.code
            HAVING research_count >= ?
            ORDER BY research_count DESC, latest_research DESC
        """, (min_count,))
        cols = [d[0] for d in cur.description]
        rows = cur.fetchall()
    finally:
        conn.close()
    return [dict(zip(cols, row)) for row in rows]


def get_concepts_for_stock(stock_code: str) -> List[str]:
    """获取某只股票关联的概念"""
    conn = get_db()
    try:
        cur = conn.cursor()
        cur.execute("SELECT concept_name FROM stock_concepts WHERE stock_code = ?", (stock_code,))
        rows = cur.fetchall()
    finally:
        conn.close()
    return [row[0] for row in rows]


def get_research_summary(stock_code: str) -> Dict[str, Any]:
    """获取某只股票的研报摘要（聚合）"""
    records = get_research_by_stock(stock_code)
    if not records:
        return {"stock_code": stock_code, "count": 0, "summary": "无研报数据"}

    # 拼接所有内容（限制总长度）
    all_content = []
    for r in records:
        title = r.get("title") or "（无标题）"
        author = r.get("author") or "未知"
        ts = r.get("publish_time", "")[:10]
        content = (r.get("content") or "")[:1000]  # 每条截 1000 字
        all_content.append(f"【{ts} | {author} | {title}】\n{content}")

    combined = "\n\n---\n\n".join(all_content)
    if len(combined) > 6000:
        combined = combined[:6000] + "\n...(已截断)"

    return {
        "stock_code": stock_code,
        "stock_name": records[0].get("stock_name", ""),
        "count": len(records),
        "latest_time": records[0].get("publish_time", ""),
        "authors": list(set(r.get("author") or "未知" for r in records)),
        "source_types": list(set(r.get("source_type") or "未知" for r in records)),
        "combined_content": combined,
        "records": records,
    }


# ============ 决策引擎集成 ============
def build_research_signals(stock_code: str) -> Dict[str, Any]:
    """
    为决策引擎构造 research_signals 输入
    兼容 debate.py 的 research_signals schema
    """
    summary = get_research_summary(stock_code)
    if summary.get("count", 0) == 0:
        return {
            "concept": "",
            "has_data": False,
            "signal_count": 0,
            "signals": [],
        }

    # 获取股票自身 description（来自 stars 表）
    conn = get_db()
    try:
        cur = conn.cursor()
        cur.execute("SELECT name, industry, sector, description FROM stocks WHERE code = ?", (stock_code,))
        row = cur.fetchone()
    finally:
        conn.close()

    if row:
        stock_name, industry, sector, stock_desc = row
    else:
        stock_name = summary.get("stock_name", "")
        industry = sector = stock_desc = ""

    # 构造信号列表
    signals = []
    for r in summary["records"]:
        signals.append({
            "source": r.get("source_type", "zsxq_post"),
            "source_id": r.get("topic_id", ""),
            "author": r.get("author", ""),
            "title": r.get("title") or "（无标题）",
            "content": r.get("content", ""),
            "publish_time": r.get("publish_time", ""),
            "sentiment_score": r.get("sentiment_score") or 0.5,
            "key_phrases": [],
            "url": r.get("source_url", ""),
        })

    return {
        "concept": sector or industry or "",
        "has_data": True,
        "signal_count": len(signals),
        "stock_code": stock_code,
        "stock_name": stock_name,
        "industry": industry,
        "sector": sector,
        "stock_description": stock_desc or "",
        "signals": signals,
    }


# ============ CLI 测试 ============
if __name__ == "__main__":
    import sys

    if len(sys.argv) > 1:
        code = sys.argv[1]
        sig = build_research_signals(code)
        print(f"=== {code} 研报信号 ===")
        print(f"概念: {sig.get('concept')}")
        print(f"信号数: {sig.get('signal_count')}")
        for i, s in enumerate(sig.get("signals", [])[:3]):
            print(f"\n[{i+1}] {s['title']}")
            print(f"    作者: {s['author']} | 时间: {s['publish_time'][:10]}")
            print(f"    内容: {s['content'][:150]}...")
    else:
        print("=== 有研报的股票（前 20）===")
        stocks = get_stocks_with_research()
        for s in stocks[:20]:
            print(f"  {s['code']:8s} {s['name']:10s} 研报: {s['research_count']:2d}  行业: {s['industry']:10s} 板块: {s['sector']}")