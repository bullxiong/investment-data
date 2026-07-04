#!/usr/bin/env python3
"""
3AI 协作投资系统 - 入口脚本
负责串行调度: GLM(数据采集) → Kimi(技术面) → MiniMax(决策引擎)

用法:
    python main.py --date 2026-07-04          # 指定日期运行
    python main.py --mode full                # 完整流水线（默认）
    python main.py --mode collect             # 只运行数据采集
    python main.py --mode technical           # 只运行技术面分析
    python main.py --mode debate              # 只运行决策辩论
    python main.py --mode dashboard           # 只生成报告
"""

import argparse
import json
import logging
import sqlite3
import sys
import time
from datetime import date, datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

# ============================================================
# 路径配置
# ============================================================
PROJECT_ROOT = Path(__file__).parent.resolve()
SRC_DIR = PROJECT_ROOT / "src"
sys.path.insert(0, str(SRC_DIR))

DB_PATH = PROJECT_ROOT / "db" / "investment.db"
DB_PATH.parent.mkdir(parents=True, exist_ok=True)

# ============================================================
# 日志
# ============================================================
LOG_FORMAT = "%(asctime)s | %(levelname)-8s | %(name)s | %(message)s"
logging.basicConfig(level=logging.INFO, format=LOG_FORMAT, handlers=[
    logging.StreamHandler(sys.stdout),
    logging.FileHandler(PROJECT_ROOT / "run.log", encoding="utf-8"),
])
logger = logging.getLogger("main")

# ============================================================
# 数据库初始化
# ============================================================
def init_db(db_path: Path = DB_PATH) -> sqlite3.Connection:
    """初始化 SQLite 数据库，执行 schema.sql"""
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    schema_path = PROJECT_ROOT / "schema.sql"
    if schema_path.exists():
        with open(schema_path, "r", encoding="utf-8") as f:
            conn.executescript(f.read())
        logger.info(f"数据库已初始化: {db_path}")
    else:
        logger.warning(f"schema.sql 未找到: {schema_path}")
    return conn


def log_run(conn: sqlite3.Connection, step: str, status: str, message: str = "", duration_ms: int = 0):
    """记录运行日志"""
    conn.execute(
        "INSERT INTO run_logs (step, status, message, duration_ms) VALUES (?, ?, ?, ?)",
        (step, status, message, duration_ms)
    )
    conn.commit()


# ============================================================
# 配置加载
# ============================================================
def load_config(config_path: Path = PROJECT_ROOT / "config.yaml") -> Dict[str, Any]:
    """加载 YAML 配置，回退到默认配置"""
    default_config = {
        "llm": {
            "provider": "openai",           # openai / glm / kimi / minimax
            "base_url": "",
            "api_key": "",
            "model": "glm-4",
        },
        "data_collection": {
            "sources": ["xueqiu", "wechat"],
            "target_date": str(date.today()),
            "xueqiu_authors": [],
            "wechat_accounts": [],
            "max_articles_per_source": 50,
        },
        "technical_analysis": {
            "stocks": [],                    # 如 ["sh.600519", "sz.000001"]
            "start_date": "2025-01-01",
            "end_date": str(date.today()),
            "strategies": ["turtle_trade", "ma_volume", "rps_breakout"],
            "backtest_window_days": 120,
        },
        "decision_engine": {
            "target_concepts": [],           # 如 ["液冷服务器", "固态电池"]
            "max_debate_rounds": 3,
            "kelly_half": True,              # 使用半Kelly
            "max_position_ratio": 0.25,      # 单标的最大仓位25%
        },
        "risk_control": {
            "max_single_stock_ratio": 0.25,
            "max_sector_ratio": 0.50,
            "max_drawdown_stop": 0.15,
        },
        "notification": {
            "enabled": False,
            "feishu_webhook": "",
            "wechat_push": False,
        },
        "dashboard": {
            "output_dir": "dashboard",
            "template": "default",
        }
    }

    if config_path.exists():
        try:
            import yaml
            with open(config_path, "r", encoding="utf-8") as f:
                user_config = yaml.safe_load(f) or {}
            # 深度合并（简单实现）
            def merge(base: dict, override: dict) -> dict:
                for k, v in override.items():
                    if k in base and isinstance(base[k], dict) and isinstance(v, dict):
                        merge(base[k], v)
                    else:
                        base[k] = v
                return base
            return merge(default_config, user_config)
        except ImportError:
            logger.warning("PyYAML 未安装，使用默认配置。安装: pip install pyyaml")
        except Exception as e:
            logger.warning(f"加载配置失败: {e}，使用默认配置")

    return default_config


# ============================================================
# Step 1: 数据采集 (GLM)
# ============================================================
def step_collect(config: Dict[str, Any], conn: sqlite3.Connection) -> Dict[str, Any]:
    """
    调用 GLM 的数据采集模块。

    期望接口:
        from src.data_collection import collect_articles, extract_signals

        articles = collect_articles(
            sources=config["sources"],
            target_date=config["target_date"],
            max_per_source=config["max_articles_per_source"]
        )  # → List[Dict] 每条包含 {source, author, title, url, content, pub_date, ...}

        signals = extract_signals(
            articles=articles,
            llm_config=config["llm"]
        )  # → List[Dict] 每条包含 {concept, related_stocks, sentiment, direction, confidence, ...}

    写入表: articles, text_signals
    """
    logger.info("=" * 60)
    logger.info("Step 1: 数据采集 (GLM)")
    logger.info("=" * 60)
    start = time.time()

    try:
        from data_collection import collect_articles, extract_signals

        dc_config = config.get("data_collection", {})
        target_date = dc_config.get("target_date", str(date.today()))

        # 1) 采集文章
        logger.info(f"开始采集文章: sources={dc_config.get('sources')}, date={target_date}")
        articles = collect_articles(
            sources=dc_config.get("sources", ["xueqiu", "wechat"]),
            target_date=target_date,
            max_per_source=dc_config.get("max_articles_per_source", 50),
        )
        logger.info(f"采集到 {len(articles)} 篇文章")

        # 写入 articles 表
        for art in articles:
            conn.execute("""
                INSERT OR IGNORE INTO articles
                (source, author, author_id, title, url, content, pub_date, category)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                art.get("source"), art.get("author"), art.get("author_id"),
                art.get("title"), art.get("url"), art.get("content"),
                art.get("pub_date"), art.get("category")
            ))
        conn.commit()

        # 2) LLM提取信号
        logger.info("开始 LLM 提取信号...")
        signals = extract_signals(
            articles=articles,
            llm_config=config.get("llm", {}),
        )
        logger.info(f"提取到 {len(signals)} 条 text_signals")

        # 写入 text_signals 表
        for sig in signals:
            conn.execute("""
                INSERT INTO text_signals
                (article_id, concept, related_stocks, sentiment, direction, confidence, extracted_date, raw_llm_output)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                sig.get("article_id"), sig.get("concept"),
                json.dumps(sig.get("related_stocks", []), ensure_ascii=False),
                sig.get("sentiment"), sig.get("direction"),
                sig.get("confidence"), target_date,
                sig.get("raw_llm_output")
            ))
        conn.commit()

        duration_ms = int((time.time() - start) * 1000)
        log_run(conn, "collect", "success", f"articles={len(articles)}, signals={len(signals)}", duration_ms)
        logger.info(f"Step 1 完成，耗时 {duration_ms}ms")
        return {"articles_count": len(articles), "signals_count": len(signals)}

    except ImportError as e:
        msg = f"GLM 数据采集模块未就绪: {e}. 请确保 src/data_collection/ 目录存在且包含 __init__.py"
        logger.error(msg)
        log_run(conn, "collect", "failure", msg)
        return {"error": msg}

    except Exception as e:
        msg = f"数据采集异常: {e}"
        logger.exception(msg)
        log_run(conn, "collect", "failure", msg)
        return {"error": msg}


# ============================================================
# Step 2: 技术面分析 (Kimi)
# ============================================================
def step_technical(config: Dict[str, Any], conn: sqlite3.Connection) -> Dict[str, Any]:
    """
    调用 Kimi 的技术面分析模块。

    期望接口:
        from src.technical_analysis import sync_stock_data, run_strategies, run_backtest

        sync_stock_data(stocks, start_date, end_date)  # → 写入 stock_daily
        signals = run_strategies(stocks, strategies, end_date)  # → 写入 strategy_signals
        backtests = run_backtest(signals, window_days)  # → 写入 backtest_results

    写入表: stock_daily, strategy_signals, backtest_results
    """
    logger.info("=" * 60)
    logger.info("Step 2: 技术面分析 (Kimi)")
    logger.info("=" * 60)
    start = time.time()

    try:
        from technical_analysis import sync_stock_data, run_strategies, run_backtest

        ta_config = config.get("technical_analysis", {})
        stocks = ta_config.get("stocks", [])
        start_date = ta_config.get("start_date", "2025-01-01")
        end_date = ta_config.get("end_date", str(date.today()))
        strategies = ta_config.get("strategies", [])

        if not stocks:
            # 如果没配置股票，尝试从 text_signals 中提取
            cursor = conn.execute(
                "SELECT DISTINCT related_stocks FROM text_signals WHERE extracted_date = ?",
                (config.get("data_collection", {}).get("target_date", str(date.today())),)
            )
            stock_set = set()
            for row in cursor.fetchall():
                try:
                    stocks_list = json.loads(row[0] or "[]")
                    stock_set.update(stocks_list)
                except json.JSONDecodeError:
                    continue
            stocks = sorted(stock_set)
            if stocks:
                logger.info(f"从 text_signals 自动推断股票列表: {stocks}")

        if not stocks:
            msg = "未配置股票列表，且 text_signals 中无相关股票"
            logger.warning(msg)
            log_run(conn, "technical", "partial", msg)
            return {"error": msg}

        # 1) 同步日K数据
        logger.info(f"同步日K数据: stocks={stocks}, range=[{start_date}, {end_date}]")
        sync_stock_data(stocks=stocks, start_date=start_date, end_date=end_date, conn=conn)

        # 2) 运行策略扫描
        logger.info(f"运行策略扫描: strategies={strategies}")
        signals = run_strategies(
            stocks=stocks,
            strategies=strategies,
            end_date=end_date,
            conn=conn,
        )
        logger.info(f"策略扫描完成，产生 {len(signals)} 条信号")

        # 3) 回测验证
        logger.info("运行回测...")
        backtests = run_backtest(
            signals=signals,
            window_days=ta_config.get("backtest_window_days", 120),
            conn=conn,
        )
        logger.info(f"回测完成，{len(backtests)} 条结果")

        duration_ms = int((time.time() - start) * 1000)
        log_run(conn, "technical", "success",
                f"stocks={len(stocks)}, signals={len(signals)}, backtests={len(backtests)}",
                duration_ms)
        logger.info(f"Step 2 完成，耗时 {duration_ms}ms")
        return {"stocks": len(stocks), "signals_count": len(signals), "backtests_count": len(backtests)}

    except ImportError as e:
        msg = f"技术面分析模块未就绪: {e}. 请确保 src/technical_analysis/ 目录存在"
        logger.error(msg)
        log_run(conn, "technical", "failure", msg)
        return {"error": msg}

    except Exception as e:
        msg = f"技术面分析异常: {e}"
        logger.exception(msg)
        log_run(conn, "technical", "failure", msg)
        return {"error": msg}


# ============================================================
# Step 3: 投研决策 (MiniMax)
# ============================================================
def step_debate(config: Dict[str, Any], conn: sqlite3.Connection) -> Dict[str, Any]:
    """
    调用 MiniMax 的投研决策模块。

    期望接口:
        from src.decision_engine import run_debate, calculate_kelly, risk_check

        debate_result = run_debate(
            text_signals=[...],       # 从 text_signals 表读取
            tech_signals=[...],       # 从 strategy_signals 表读取
            target_concepts=[...],    # 要决策的概念列表
            target_date="2026-07-04",
            llm_config=config["llm"],
        )
        # → Dict: {
        #     "concept": "液冷服务器",
        #     "direction": "long",
        #     "confidence": 0.78,
        #     "claims": [...],
        #     ...
        # }

        kelly = calculate_kelly(confidence=0.78, upside=8.0, downside=4.0)
        final = risk_check(kelly, portfolio, risk_config)

    写入表: decisions, debate_logs
    """
    logger.info("=" * 60)
    logger.info("Step 3: 投研决策 (MiniMax)")
    logger.info("=" * 60)
    start = time.time()

    try:
        from decision_engine import run_debate, calculate_kelly, risk_check

        de_config = config.get("decision_engine", {})
        target_date = config.get("data_collection", {}).get("target_date", str(date.today()))
        target_concepts = de_config.get("target_concepts", [])

        # 如果没指定概念，从 text_signals 中自动提取
        if not target_concepts:
            cursor = conn.execute(
                "SELECT DISTINCT concept FROM text_signals WHERE extracted_date = ? AND sentiment != 'neutral'",
                (target_date,)
            )
            target_concepts = [row[0] for row in cursor.fetchall() if row[0]]
            logger.info(f"自动提取关注概念: {target_concepts}")

        if not target_concepts:
            msg = "未配置关注概念，且 text_signals 中无可决策概念"
            logger.warning(msg)
            log_run(conn, "debate", "partial", msg)
            return {"error": msg}

        # 读取 GLM 的文本信号
        cursor = conn.execute("""
            SELECT concept, related_stocks, sentiment, direction, confidence
            FROM text_signals WHERE extracted_date = ?
        """, (target_date,))
        text_signals = [
            dict(row) for row in cursor.fetchall()
        ]

        # 读取 Kimi 的技术面信号
        cursor = conn.execute("""
            SELECT code, strategy_name, action, trigger_price, target_price, stop_price, strength
            FROM strategy_signals WHERE signal_date = ?
        """, (target_date,))
        tech_signals = [
            dict(row) for row in cursor.fetchall()
        ]

        results = []
        for concept in target_concepts:
            logger.info(f"开始辩论概念: {concept}")

            # 过滤相关信号
            concept_text_signals = [s for s in text_signals if s.get("concept") == concept]
            related_stocks = set()
            for s in concept_text_signals:
                try:
                    related_stocks.update(json.loads(s.get("related_stocks", "[]") or "[]"))
                except:
                    pass
            concept_tech_signals = [s for s in tech_signals if s.get("code") in related_stocks]

            # 多Agent辩论
            debate_result = run_debate(
                text_signals=concept_text_signals,
                tech_signals=concept_tech_signals,
                target_concept=concept,
                target_date=target_date,
                llm_config=config.get("llm", {}),
                max_rounds=de_config.get("max_debate_rounds", 3),
            )

            # Kelly仓位
            confidence = debate_result.get("confidence", 0.5)
            entry = debate_result.get("entry_price", 0)
            target = debate_result.get("target_price", 0)
            stop = debate_result.get("stop_loss", 0)

            if target > entry > stop > 0:
                upside = target - entry
                downside = entry - stop
                kelly = calculate_kelly(confidence=confidence, upside=upside, downside=downside)
                if de_config.get("kelly_half", True):
                    kelly = kelly * 0.5
                max_pos = config.get("risk_control", {}).get("max_single_stock_ratio", 0.25)
                final_position = min(kelly, max_pos)
            else:
                kelly = 0.0
                final_position = 0.0

            # 风控门控
            risk_ok = risk_check(
                position=final_position,
                portfolio={},  # TODO: 从数据库读取当前持仓
                risk_config=config.get("risk_control", {}),
            )

            if not risk_ok:
                final_position = 0.0
                logger.warning(f"{concept} 未通过风控门控，仓位归零")

            # 写入 decisions
            cursor = conn.execute("""
                INSERT INTO decisions
                (concept, code, target_date, direction, confidence, kelly_ratio, final_position,
                 entry_price, target_price, stop_loss, risk_level, status)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                concept,
                ",".join(sorted(related_stocks)) if related_stocks else None,
                target_date,
                debate_result.get("direction", "neutral"),
                confidence,
                kelly,
                final_position,
                entry,
                target,
                stop,
                debate_result.get("risk_level", "medium"),
                "active"
            ))
            decision_id = cursor.lastrowid

            # 写入 debate_logs
            for claim in debate_result.get("claims", []):
                conn.execute("""
                    INSERT INTO debate_logs
                    (decision_id, round, agent_role, claim_type, content, confidence)
                    VALUES (?, ?, ?, ?, ?, ?)
                """, (
                    decision_id,
                    claim.get("round"),
                    claim.get("agent_role"),
                    claim.get("claim_type"),
                    claim.get("content"),
                    claim.get("confidence")
                ))

            conn.commit()
            logger.info(f"  {concept}: direction={debate_result.get('direction')}, "
                       f"confidence={confidence:.2f}, kelly={kelly:.2%}, final={final_position:.2%}")
            results.append({
                "concept": concept,
                "direction": debate_result.get("direction"),
                "confidence": confidence,
                "kelly": kelly,
                "final_position": final_position,
            })

        duration_ms = int((time.time() - start) * 1000)
        log_run(conn, "debate", "success", f"concepts={len(target_concepts)}", duration_ms)
        logger.info(f"Step 3 完成，耗时 {duration_ms}ms")
        return {"concepts": len(target_concepts), "results": results}

    except ImportError as e:
        msg = f"决策引擎模块未就绪: {e}. 请确保 src/decision_engine/ 目录存在"
        logger.error(msg)
        log_run(conn, "debate", "failure", msg)
        return {"error": msg}

    except Exception as e:
        msg = f"决策引擎异常: {e}"
        logger.exception(msg)
        log_run(conn, "debate", "failure", msg)
        return {"error": msg}


# ============================================================
# Step 4: 生成输出 (Dashboard + 通知)
# ============================================================
def step_generate(config: Dict[str, Any], conn: sqlite3.Connection) -> Dict[str, Any]:
    """
    生成 Dashboard HTML 和通知推送。

    期望接口:
        from src.integration.dashboard import generate_dashboard
        from src.integration.fusion import fusion_report

        report = fusion_report(conn, target_date)
        generate_dashboard(report, output_dir)
    """
    logger.info("=" * 60)
    logger.info("Step 4: 生成输出")
    logger.info("=" * 60)
    start = time.time()

    try:
        from integration.dashboard import generate_dashboard
        from integration.fusion import fusion_report

        target_date = config.get("data_collection", {}).get("target_date", str(date.today()))
        output_dir = PROJECT_ROOT / config.get("dashboard", {}).get("output_dir", "dashboard")
        output_dir.mkdir(parents=True, exist_ok=True)

        # 三因子融合报告
        logger.info("生成融合报告...")
        report = fusion_report(conn=conn, target_date=target_date)

        # 生成 HTML Dashboard
        dashboard_path = output_dir / f"dashboard_{target_date}.html"
        generate_dashboard(report, str(dashboard_path))
        logger.info(f"Dashboard 已生成: {dashboard_path}")

        # TODO: 飞书/微信通知推送
        notif = config.get("notification", {})
        if notif.get("enabled"):
            logger.info("通知推送: TODO")

        duration_ms = int((time.time() - start) * 1000)
        log_run(conn, "generate", "success", f"dashboard={dashboard_path}", duration_ms)
        logger.info(f"Step 4 完成，耗时 {duration_ms}ms")
        return {"dashboard": str(dashboard_path), "report": report}

    except ImportError as e:
        msg = f"集成模块未就绪: {e}. 请确保 src/integration/ 目录存在"
        logger.error(msg)
        log_run(conn, "generate", "failure", msg)
        return {"error": msg}

    except Exception as e:
        msg = f"生成输出异常: {e}"
        logger.exception(msg)
        log_run(conn, "generate", "failure", msg)
        return {"error": msg}


# ============================================================
# 主流程
# ============================================================
def main():
    parser = argparse.ArgumentParser(description="3AI 协作投资系统")
    parser.add_argument("--date", type=str, default=str(date.today()),
                        help="目标日期 (YYYY-MM-DD)")
    parser.add_argument("--mode", type=str, default="full",
                        choices=["full", "collect", "technical", "debate", "dashboard"],
                        help="运行模式")
    parser.add_argument("--config", type=str, default="config.yaml",
                        help="配置文件路径")
    args = parser.parse_args()

    logger.info("=" * 60)
    logger.info("3AI 协作投资系统启动")
    logger.info(f"模式: {args.mode}, 日期: {args.date}")
    logger.info("=" * 60)

    # 加载配置
    config_path = PROJECT_ROOT / args.config
    config = load_config(config_path)
    config["data_collection"]["target_date"] = args.date
    config["technical_analysis"]["end_date"] = args.date

    # 初始化数据库
    conn = init_db()

    # 根据模式执行
    results = {}

    if args.mode in ("full", "collect"):
        results["collect"] = step_collect(config, conn)
        if "error" in results["collect"]:
            logger.error("数据采集失败，后续步骤可能受影响")

    if args.mode in ("full", "technical"):
        results["technical"] = step_technical(config, conn)
        if "error" in results.get("technical", {}):
            logger.error("技术面分析失败，后续步骤可能受影响")

    if args.mode in ("full", "debate"):
        results["debate"] = step_debate(config, conn)
        if "error" in results.get("debate", {}):
            logger.error("决策辩论失败")

    if args.mode in ("full", "dashboard"):
        results["generate"] = step_generate(config, conn)

    # 关闭数据库
    conn.close()

    # 汇总
    logger.info("=" * 60)
    logger.info("运行汇总")
    logger.info("=" * 60)
    for step, result in results.items():
        status = "✅" if "error" not in result else "❌"
        logger.info(f"  {status} {step}: {result}")

    # 如果有错误，返回非0退出码
    if any("error" in r for r in results.values()):
        sys.exit(1)


if __name__ == "__main__":
    main()
