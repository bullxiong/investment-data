#!/usr/bin/env python3
"""验证 technical_analysis 和 integration 模块"""
import sys, sqlite3
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent / "src"))

from datetime import date
from main import init_db, load_config
from technical_analysis import sync_stock_data, run_strategies, run_backtest
from integration import fusion_report, generate_dashboard

DB_PATH = Path("db/test_verify.db")
if DB_PATH.exists(): DB_PATH.unlink()

conn = init_db(DB_PATH)
config = load_config()

print("="*60)
print("验证 technical_analysis 模块")
print("="*60)

# 1. 同步数据（mock模式）
print("\n1. sync_stock_data ...")
sync_stock_data(
    stocks=["sh.600519", "sz.000001"],
    start_date="2025-01-01",
    end_date=str(date.today()),
    conn=conn,
)

# 检查数据
cursor = conn.execute("SELECT COUNT(*) FROM stock_daily")
count = cursor.fetchone()[0]
print(f"   stock_daily 记录数: {count}")

# 2. 运行策略
print("\n2. run_strategies ...")
signals = run_strategies(
    stocks=["sh.600519", "sz.000001"],
    strategies=["turtle_trade", "ma_volume", "rps_breakout"],
    end_date=str(date.today()),
    conn=conn,
)
print(f"   策略信号: {len(signals)} 条")
for s in signals[:3]:
    print(f"   - {s['code']} / {s['strategy_name']}: {s['action']} @ {s['trigger_price']}")

# 3. 回测
print("\n3. run_backtest ...")
backtests = run_backtest(signals=signals, window_days=30, conn=conn)
print(f"   回测结果: {len(backtests)} 条")
for b in backtests[:3]:
    print(f"   - {b['code']} / {b['strategy_name']}: 收益={b['total_return']:.2%}, 最大回撤={b['max_drawdown']:.2%}")

print("\n" + "="*60)
print("验证 integration 模块")
print("="*60)

# 4. 融合报告
print("\n4. fusion_report ...")
# 先插入一些测试数据到 text_signals 和 decisions
conn.execute("""
    INSERT INTO text_signals (article_id, concept, related_stocks, sentiment, direction, confidence, extracted_date)
    VALUES (1, '液冷服务器', '["sh.600519"]', 'bullish', 'long', 0.78, ?)
""", (str(date.today()),))
conn.execute("""
    INSERT INTO decisions (concept, code, target_date, direction, confidence, kelly_ratio, final_position, entry_price, target_price, stop_loss, risk_level, status)
    VALUES ('液冷服务器', 'sh.600519', ?, 'long', 0.75, 0.15, 0.10, 1500, 1650, 1400, 'low', 'active')
""", (str(date.today()),))
conn.commit()

report = fusion_report(conn=conn, target_date=str(date.today()))
print(f"   推荐数: {len(report['recommendations'])}")
for r in report['recommendations'][:3]:
    print(f"   - {r['concept']}: {r['level']} (综合={r['composite_score']:.2f}, 共振={r['resonance']})")

# 5. 生成 Dashboard
print("\n5. generate_dashboard ...")
output_path = "dashboard/test_verify.html"
generate_dashboard(report=report, output_path=output_path)
print(f"   Dashboard: {output_path}")

conn.close()
print("\n" + "="*60)
print("验证完成！")
print("="*60)
