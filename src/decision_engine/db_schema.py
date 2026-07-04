"""
db_schema.py - Decision Engine 数据库 Schema 与持久化

两张表：
- decisions：每条决策汇总
- debate_logs：每个 Agent 每轮的 Claim/Evidence

接口：
- init_db(db_path) -> Connection
- save_decision(conn, decision_dict) -> decision_id
- save_debate_log(conn, decision_id, log_dict)
- list_decisions(conn, limit=20) -> list
- get_debate_logs(conn, decision_id) -> list
"""

from __future__ import annotations
import json
import sqlite3
from pathlib import Path
from typing import Any, Dict, List, Optional


# ============ Schema 定义 ============
SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS decisions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    date TEXT,
    concept TEXT,
    direction TEXT,                 -- 'bullish' | 'bearish' | 'neutral'
    confidence REAL,                -- 0.0~1.0
    entry_price REAL,
    target_price REAL,
    stop_loss REAL,
    kelly_position REAL,            -- Kelly 算出的理论仓位
    final_position REAL,            -- 风控门控后的最终仓位
    rationale TEXT,
    created_at TEXT DEFAULT (datetime('now', 'localtime'))
);

CREATE TABLE IF NOT EXISTS debate_logs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    decision_id INTEGER,
    agent_name TEXT,                -- 'intel_researcher' | 'bull_researcher' | ...
    claim TEXT,                     -- 主张/Claim
    evidence TEXT,                  -- 证据
    confidence REAL,                -- 该轮的置信度
    round INTEGER,                  -- 辩论轮次（1~6）
    created_at TEXT DEFAULT (datetime('now', 'localtime')),
    FOREIGN KEY (decision_id) REFERENCES decisions(id)
);

CREATE INDEX IF NOT EXISTS idx_decisions_concept ON decisions(concept);
CREATE INDEX IF NOT EXISTS idx_decisions_date ON decisions(date);
CREATE INDEX IF NOT EXISTS idx_debate_logs_decision ON debate_logs(decision_id);
CREATE INDEX IF NOT EXISTS idx_debate_logs_agent ON debate_logs(agent_name);
"""


# ============ 初始化 ============
DEFAULT_DB_PATH = "/workspace/src/decision_engine/decisions.db"


def init_db(db_path: str = DEFAULT_DB_PATH) -> sqlite3.Connection:
    """初始化数据库 + 创建表

    Args:
        db_path: SQLite 数据库路径（默认本地文件）

    Returns:
        sqlite3.Connection（不要 close，让调用方管理）
    """
    # 确保目录存在
    Path(db_path).parent.mkdir(parents=True, exist_ok=True)

    conn = sqlite3.connect(db_path)
    conn.executescript(SCHEMA_SQL)
    conn.commit()
    return conn


# ============ 写入 ============
def save_decision(conn: sqlite3.Connection, decision: Dict[str, Any]) -> int:
    """写入一条决策

    Args:
        conn: 数据库连接
        decision: {
            "date": "2026-06-29",
            "concept": "半导体",
            "direction": "bullish",
            "confidence": 0.75,
            "entry_price": 42.5,
            "target_price": 48.0,
            "stop_loss": 40.0,
            "kelly_position": 0.18,
            "final_position": 0.15,
            "rationale": "..."
        }

    Returns:
        decision_id（自增 ID）
    """
    cur = conn.cursor()
    cur.execute("""
        INSERT INTO decisions
        (date, concept, direction, confidence, entry_price, target_price,
         stop_loss, kelly_position, final_position, rationale)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (
        decision.get("date"),
        decision.get("concept"),
        decision.get("direction"),
        decision.get("confidence"),
        decision.get("entry_price"),
        decision.get("target_price"),
        decision.get("stop_loss"),
        decision.get("kelly_position"),
        decision.get("final_position"),
        decision.get("rationale"),
    ))
    conn.commit()
    return cur.lastrowid


def save_debate_log(conn: sqlite3.Connection, decision_id: int,
                     log: Dict[str, Any]) -> int:
    """写入一条辩论日志

    Args:
        conn: 数据库连接
        decision_id: 关联的决策 ID
        log: {
            "agent_name": "bull_researcher",
            "claim": "半导体行业 28nm 国产替代加速",
            "evidence": "研报：长江证券 2026-06-28 提到...",
            "confidence": 0.75,
            "round": 2
        }

    Returns:
        log_id
    """
    cur = conn.cursor()
    cur.execute("""
        INSERT INTO debate_logs
        (decision_id, agent_name, claim, evidence, confidence, round)
        VALUES (?, ?, ?, ?, ?, ?)
    """, (
        decision_id,
        log.get("agent_name"),
        log.get("claim"),
        log.get("evidence"),
        log.get("confidence"),
        log.get("round"),
    ))
    conn.commit()
    return cur.lastrowid


def save_debate_logs(conn: sqlite3.Connection, decision_id: int,
                      logs: List[Dict[str, Any]]) -> List[int]:
    """批量写入辩论日志"""
    return [save_debate_log(conn, decision_id, log) for log in logs]


# ============ 查询 ============
def list_decisions(conn: sqlite3.Connection, limit: int = 20,
                    concept: Optional[str] = None) -> List[Dict]:
    """列出最近决策（按时间倒序）"""
    cur = conn.cursor()
    if concept:
        cur.execute("""
            SELECT id, date, concept, direction, confidence,
                   entry_price, target_price, stop_loss,
                   kelly_position, final_position, rationale, created_at
            FROM decisions
            WHERE concept = ?
            ORDER BY created_at DESC
            LIMIT ?
        """, (concept, limit))
    else:
        cur.execute("""
            SELECT id, date, concept, direction, confidence,
                   entry_price, target_price, stop_loss,
                   kelly_position, final_position, rationale, created_at
            FROM decisions
            ORDER BY created_at DESC
            LIMIT ?
        """, (limit,))

    cols = [d[0] for d in cur.description]
    return [dict(zip(cols, row)) for row in cur.fetchall()]


def get_debate_logs(conn: sqlite3.Connection, decision_id: int) -> List[Dict]:
    """获取某决策的完整辩论日志（按 round 排序）"""
    cur = conn.cursor()
    cur.execute("""
        SELECT id, decision_id, agent_name, claim, evidence,
               confidence, round, created_at
        FROM debate_logs
        WHERE decision_id = ?
        ORDER BY round ASC, id ASC
    """, (decision_id,))
    cols = [d[0] for d in cur.description]
    return [dict(zip(cols, row)) for row in cur.fetchall()]


# ============ 测试 ============
if __name__ == "__main__":
    print("=== 测试 db_schema.py ===\n")

    conn = init_db()

    # 写一条决策
    decision_id = save_decision(conn, {
        "date": "2026-07-01",
        "concept": "半导体",
        "direction": "bullish",
        "confidence": 0.75,
        "entry_price": 42.5,
        "target_price": 48.0,
        "stop_loss": 40.0,
        "kelly_position": 0.18,
        "final_position": 0.15,
        "rationale": "国产替代 + AI 算力需求",
    })
    print(f"✅ 写决策 ID={decision_id}")

    # 写 6 条辩论日志（对应 6 个角色）
    logs = [
        {"agent_name": "intel_researcher", "claim": "整合了 12 条 GLM 数据 + 5 条 Kimi 指标",
         "evidence": "text_signals 12 条 / tech_signals 5 条", "confidence": 0.70, "round": 1},
        {"agent_name": "bull_researcher", "claim": "国产替代加速 + 28nm 扩产",
         "evidence": "长江证券 2026-06-28 研报", "confidence": 0.80, "round": 2},
        {"agent_name": "bear_researcher", "claim": "短期涨幅过大, RS 临近超买",
         "evidence": "RSI=72 + 30 日累计 +35%", "confidence": 0.55, "round": 3},
        {"agent_name": "value_chain_agent", "claim": "产业链景气度高, 设备/材料订单饱满",
         "evidence": "中芯国际 28nm 产能利用率 95%", "confidence": 0.75, "round": 4},
        {"agent_name": "risk_researcher", "claim": "半 Kelly 仓位 18%, 25% 上限, 通过",
         "evidence": "f* = (0.75*0.15 - 0.25)/(0.15) = 0.13, × 0.5 = 0.18", "confidence": 0.80, "round": 5},
        {"agent_name": "arbiter_researcher", "claim": "bull 0.80 > bear 0.55 → bullish, 仓位 15%",
         "evidence": "最终决策 bullish, 置信度 0.75", "confidence": 0.75, "round": 6},
    ]
    save_debate_logs(conn, decision_id, logs)
    print(f"✅ 写 {len(logs)} 条辩论日志")

    # 读出来验证
    print("\n=== 最近决策 ===")
    decisions = list_decisions(conn, limit=5)
    for d in decisions:
        print(f"  [{d['id']}] {d['date']} {d['concept']} {d['direction']} conf={d['confidence']:.2f} 仓位={d['final_position']*100:.0f}%")

    print(f"\n=== 决策 #{decision_id} 完整辩论 ===")
    for log in get_debate_logs(conn, decision_id):
        print(f"  Round {log['round']} {log['agent_name']}: {log['claim'][:40]}... (conf={log['confidence']:.2f})")

    conn.close()
    print("\n✅ 全部完成")