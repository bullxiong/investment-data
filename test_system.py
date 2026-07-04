#!/usr/bin/env python3
"""
3AI 系统测试脚本
================
渐进式测试：从环境 → 数据库 → 模块 → 入口

用法:
    python test_system.py           # 运行全部测试
    python test_system.py --level 3  # 只运行到 L3
"""

import argparse
import json
import os
import sqlite3
import sys
import traceback
from datetime import date
from pathlib import Path
from typing import Any, Dict, List, Tuple

PROJECT_ROOT = Path(__file__).parent.resolve()
SRC_DIR = PROJECT_ROOT / "src"
sys.path.insert(0, str(SRC_DIR))

DB_TEST_PATH = PROJECT_ROOT / "db" / "test_investment.db"

RESULTS: List[Dict[str, Any]] = []


def log(level: str, name: str, status: str, detail: str = ""):
    """记录测试结果"""
    RESULTS.append({
        "level": level,
        "name": name,
        "status": status,
        "detail": detail,
    })
    icon = "[OK]" if status == "PASS" else "[FAIL]" if status == "FAIL" else "[WARN]"
    print(f"  {icon} [{level}] {name}")
    if detail:
        for line in detail.split("\n"):
            print(f"      {line}")


def test_l1_environment():
    """L1: 环境检查"""
    print("\n" + "=" * 60)
    print("L1: 环境检查")
    print("=" * 60)

    # Python 版本
    py_version = sys.version_info
    if py_version >= (3, 9):
        log("L1", f"Python {py_version.major}.{py_version.minor}.{py_version.micro}", "PASS")
    else:
        log("L1", f"Python {py_version.major}.{py_version.minor}", "FAIL", "需要 Python 3.9+")

    # SQLite3
    try:
        import sqlite3
        log("L1", f"sqlite3 {sqlite3.sqlite_version}", "PASS")
    except Exception as e:
        log("L1", "sqlite3", "FAIL", str(e))

    # 关键依赖检查
    deps = [
        ("requests", "requests"),
        ("pandas", "pandas"),
        ("numpy", "numpy"),
    ]
    for name, module in deps:
        try:
            __import__(module)
            log("L1", f"依赖: {name}", "PASS")
        except ImportError:
            log("L1", f"依赖: {name}", "FAIL", "pip install " + name)

    # 路径检查
    for path in ["src", "src/core", "src/data_collection", "src/technical_analysis", "src/decision_engine", "src/integration"]:
        p = PROJECT_ROOT / path
        if p.exists():
            log("L1", f"目录: {path}", "PASS")
        else:
            log("L1", f"目录: {path}", "FAIL", "目录不存在")

    # 文件检查
    for file in ["main.py", "schema.sql", "config.yaml", "requirements.txt"]:
        p = PROJECT_ROOT / file
        if p.exists():
            log("L1", f"文件: {file}", "PASS")
        else:
            log("L1", f"文件: {file}", "FAIL", "文件不存在")


def test_l2_database():
    """L2: 数据库测试"""
    print("\n" + "=" * 60)
    print("L2: 数据库测试")
    print("=" * 60)

    # 删除旧测试库
    if DB_TEST_PATH.exists():
        DB_TEST_PATH.unlink()

    try:
        conn = sqlite3.connect(str(DB_TEST_PATH))
        conn.row_factory = sqlite3.Row

        # 执行 schema.sql
        schema_path = PROJECT_ROOT / "schema.sql"
        if schema_path.exists():
            with open(schema_path, "r", encoding="utf-8") as f:
                conn.executescript(f.read())
            log("L2", "schema.sql 执行", "PASS")
        else:
            log("L2", "schema.sql 执行", "FAIL", "schema.sql 不存在")
            return

        # 检查表是否存在
        cursor = conn.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")
        tables = [row[0] for row in cursor.fetchall()]
        expected = ["articles", "backtest_results", "debate_logs", "decisions", "run_logs", "stock_daily", "strategy_signals", "text_signals"]
        missing = [t for t in expected if t not in tables]
        if not missing:
            log("L2", f"8 张表全部创建", "PASS", f"tables: {tables}")
        else:
            log("L2", f"表检查", "FAIL", f"缺失: {missing}, 实际: {tables}")

        # 测试写入 articles
        conn.execute("""
            INSERT INTO articles (source, author, title, url, content, pub_date, category)
            VALUES (?, ?, ?, ?, ?, ?, ?)
        """, ("xueqiu", "test", "测试文章", "https://test", "内容", "2026-07-04", "test"))
        conn.commit()

        cursor = conn.execute("SELECT COUNT(*) FROM articles")
        count = cursor.fetchone()[0]
        if count == 1:
            log("L2", "articles 写入测试", "PASS")
        else:
            log("L2", "articles 写入测试", "FAIL", f"count={count}")

        # 测试写入 text_signals
        conn.execute("""
            INSERT INTO text_signals (article_id, concept, related_stocks, sentiment, direction, confidence, extracted_date)
            VALUES (?, ?, ?, ?, ?, ?, ?)
        """, (1, "测试概念", '["000001"]', "bullish", "long", 0.75, "2026-07-04"))
        conn.commit()
        log("L2", "text_signals 写入测试", "PASS")

        # 测试写入 decisions
        conn.execute("""
            INSERT INTO decisions (concept, target_date, direction, confidence, kelly_ratio, final_position, risk_level)
            VALUES (?, ?, ?, ?, ?, ?, ?)
        """, ("测试概念", "2026-07-04", "long", 0.75, 0.15, 0.10, "low"))
        conn.commit()
        log("L2", "decisions 写入测试", "PASS")

        conn.close()

    except Exception as e:
        log("L2", "数据库测试", "FAIL", f"{type(e).__name__}: {e}")
        traceback.print_exc()


def test_l3_decision_engine():
    """L3: 决策引擎测试"""
    print("\n" + "=" * 60)
    print("L3: 决策引擎 (MiniMax) 测试")
    print("=" * 60)

    # 1. 检查模块导入
    try:
        from decision_engine import run_debate, calculate_kelly, risk_check
        log("L3", "模块导入", "PASS")
    except Exception as e:
        log("L3", "模块导入", "FAIL", f"{type(e).__name__}: {e}")
        traceback.print_exc()
        return

    # 2. Kelly 计算测试
    try:
        k = calculate_kelly(confidence=0.7, upside=15, downside=5)
        # 期望值: (0.7*15 - 0.3*5)/15 = 0.6, 半Kelly = 0.3, 上限 0.25 → 0.25
        if 0 < k <= 0.25:
            log("L3", f"Kelly 计算 (conf=0.7, up=15, down=5) → {k:.4f}", "PASS")
        else:
            log("L3", f"Kelly 计算 → {k:.4f}", "FAIL", "超出预期范围 (0, 0.25]")
    except Exception as e:
        log("L3", "Kelly 计算", "FAIL", f"{type(e).__name__}: {e}")

    # 3. 风控检查测试
    try:
        ok = risk_check(
            position=0.15,
            portfolio={},
            risk_config={"max_single_stock_ratio": 0.25, "max_sector_ratio": 0.30, "max_drawdown_stop": 0.15}
        )
        if ok is True:
            log("L3", "风控检查 (position=0.15)", "PASS")
        else:
            log("L3", "风控检查", "FAIL", f"期望 True, 得到 {ok}")
    except Exception as e:
        log("L3", "风控检查", "FAIL", f"{type(e).__name__}: {e}")

    # 4. 模拟 debates 测试（需要 LLM，可能跳过）
    try:
        text_signals = [
            {"article_id": 1, "concept": "液冷服务器", "related_stocks": '["002594", "300502"]', "sentiment": "bullish", "direction": "long", "confidence": 0.78, "extracted_date": "2026-07-04"}
        ]
        tech_signals = [
            {"code": "sh.600519", "strategy_name": "turtle_trade", "action": "buy", "trigger_price": 1500, "target_price": 1650, "stop_price": 1400, "strength": 0.8}
        ]

        result = run_debate(
            text_signals=text_signals,
            tech_signals=tech_signals,
            target_concept="液冷服务器",
            target_date="2026-07-04",
            llm_config={"provider": "deepseek", "model": "deepseek-chat"},
        )

        if isinstance(result, dict) and "direction" in result:
            log("L3", f"run_debate 返回: direction={result['direction']}, conf={result.get('confidence')}", "PASS")
        else:
            log("L3", "run_debate 返回", "FAIL", f"返回值: {type(result)}")
    except Exception as e:
        log("L3", "run_debate 模拟测试", "FAIL", f"{type(e).__name__}: {e}")
        traceback.print_exc()


def test_l4_main_entry():
    """L4: 入口脚本测试"""
    print("\n" + "=" * 60)
    print("L4: 入口脚本 (main.py) 测试")
    print("=" * 60)

    # 1. main.py 语法检查
    try:
        with open(PROJECT_ROOT / "main.py", "r", encoding="utf-8") as f:
            code = f.read()
        compile(code, "main.py", "exec")
        log("L4", "main.py 语法检查", "PASS")
    except SyntaxError as e:
        log("L4", "main.py 语法检查", "FAIL", f"Line {e.lineno}: {e.msg}")

    # 2. 配置加载测试
    try:
        sys.path.insert(0, str(PROJECT_ROOT))
        import importlib
        main = importlib.import_module("main")
        config = main.load_config()
        if "llm" in config and "data_collection" in config:
            log("L4", "配置加载", "PASS")
        else:
            log("L4", "配置加载", "FAIL", "配置缺少关键字段")
    except Exception as e:
        log("L4", "配置加载", "FAIL", f"{type(e).__name__}: {e}")

    # 3. 数据库初始化测试
    try:
        if DB_TEST_PATH.exists():
            DB_TEST_PATH.unlink()
        conn = main.init_db(DB_TEST_PATH)
        cursor = conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
        tables = [row[0] for row in cursor.fetchall()]
        conn.close()
        if len(tables) >= 8:
            log("L4", "init_db() 初始化", "PASS", f"{len(tables)} 张表")
        else:
            log("L4", "init_db() 初始化", "FAIL", f"只有 {len(tables)} 张表")
    except Exception as e:
        log("L4", "init_db() 初始化", "FAIL", f"{type(e).__name__}: {e}")


def test_l5_full_pipeline():
    """L5: 完整流水线测试（预期会失败）"""
    print("\n" + "=" * 60)
    print("L5: 完整流水线测试（预期 GLM/Kimi 模块未实现）")
    print("=" * 60)

    try:
        sys.path.insert(0, str(PROJECT_ROOT))
        import importlib
        main = importlib.import_module("main")

        # 尝试调用 step_collect（预期失败，因为 GLM 未实现）
        config = main.load_config()
        config["data_collection"]["target_date"] = str(date.today())
        config["technical_analysis"]["end_date"] = str(date.today())

        conn = main.init_db(DB_TEST_PATH)
        result = main.step_collect(config, conn)
        conn.close()

        if "error" in result:
            log("L5", "step_collect", "EXPECTED_FAIL", result["error"])
        else:
            log("L5", "step_collect", "PASS", f"articles={result.get('articles_count')}")
    except Exception as e:
        log("L5", "step_collect", "FAIL", f"{type(e).__name__}: {e}")


def print_summary():
    """打印测试摘要"""
    print("\n" + "=" * 60)
    print("测试摘要")
    print("=" * 60)

    total = len(RESULTS)
    passed = sum(1 for r in RESULTS if r["status"] == "PASS")
    failed = sum(1 for r in RESULTS if r["status"] == "FAIL")
    expected_fail = sum(1 for r in RESULTS if r["status"] == "EXPECTED_FAIL")

    print(f"总计: {total} 项 | [OK] 通过: {passed} | [FAIL] 失败: {failed} | [WARN] 预期失败: {expected_fail}")
    print()

    if failed > 0:
        print("需要修复的问题：")
        for r in RESULTS:
            if r["status"] == "FAIL":
                print(f"  - [{r['level']}] {r['name']}: {r['detail']}")
        print()

    if expected_fail > 0:
        print("预期失败（等待其他模块实现）：")
        for r in RESULTS:
            if r["status"] == "EXPECTED_FAIL":
                print(f"  - [{r['level']}] {r['name']}: {r['detail']}")
        print()

    print("=" * 60)
    print("建议行动项：")
    if failed == 0 and expected_fail > 0:
        print("  - 所有基础测试通过，等待 GLM 和 Kimi 实现各自模块")
        print("  - 可以先独立测试 MiniMax 决策引擎")
    elif failed > 0:
        print("  - 先修复 FAIL 项，再等待其他模块")
    else:
        print("  - 所有测试通过！可以跑完整流水线")
    print("=" * 60)


def main():
    parser = argparse.ArgumentParser(description="3AI 系统测试")
    parser.add_argument("--level", type=int, default=5, choices=[1, 2, 3, 4, 5],
                        help="最大测试层级 (1-5)")
    args = parser.parse_args()

    print("3AI 协作投资系统 - 测试脚本")
    print(f"项目根目录: {PROJECT_ROOT}")
    print(f"测试数据库: {DB_TEST_PATH}")

    if args.level >= 1:
        test_l1_environment()
    if args.level >= 2:
        test_l2_database()
    if args.level >= 3:
        test_l3_decision_engine()
    if args.level >= 4:
        test_l4_main_entry()
    if args.level >= 5:
        test_l5_full_pipeline()

    print_summary()


if __name__ == "__main__":
    main()
