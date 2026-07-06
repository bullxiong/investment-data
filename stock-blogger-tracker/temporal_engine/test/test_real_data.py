"""
真实数据测试: 云起 191 条信号 + 模拟博主数据 → 时序漏斗
"""
import sys, os, json

PROJECT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
STB = os.path.dirname(PROJECT)  # stock-blogger-tracker root
sys.path.insert(0, STB)

WORK_DIR = os.path.dirname(os.path.abspath(__file__))

from temporal_engine.db import init_db, get_conn
from temporal_engine.cloud_adapter import load_cloud_signals
from temporal_engine.test.seed_data import generate as seed_blogger_data
from temporal_engine.daily_funnel import run as run_funnel


def main():
    print('=' * 60)
    print(' REAL DATA TEST: Cloud + Blogger -> Funnel')
    print('=' * 60)
    print()

    # Step 1: 加载真实云起数据
    print('[1] Loading cloud signals...')
    cloud_data = load_cloud_signals()
    print('    Concepts with cloud signals: %s' % list(cloud_data.keys()))
    print()

    # Step 2: 生成模拟博主数据
    print('[2] Seeding blogger data...')
    init_db()
    # 清空旧数据
    conn = get_conn()
    conn.execute("DELETE FROM signal_timeline")
    conn.commit()
    conn.close()
    seed_blogger_data()
    print()

    # Step 3: 跑漏斗 (用最新博主数据日期 2026-07-04)
    print('[3] Running funnel...')
    result = run_funnel('2026-07-04', cloud_data)
    print()

    # Step 4: 输出结果
    print('=' * 60)
    print(' RESULTS')
    print('=' * 60)
    print('Layer 1 (active signals): %d concepts' % result['layer1_total'])
    print('Layer 2 (after stage filter): %d concepts' % result['layer2_after_filter'])
    print('Layer 5 (final candidates): %d concepts' % len(result['layer5_candidates']))
    print()
    print('Avoid list (%d): %s' % (len(result['avoid']), result['avoid']))
    print('Watch list (%d): %s' % (len(result['watch']), result['watch']))
    print()
    print(result['report_text'])
    print()

    # Step 5: 保存完整JSON
    out_path = os.path.join(WORK_DIR, 'real_data_output.json')
    with open(out_path, 'w', encoding='utf-8') as f:
        json.dump({
            'date': result['date'],
            'layer1_total': result['layer1_total'],
            'layer2_after_filter': result['layer2_after_filter'],
            'candidates': result['layer5_candidates'],
            'avoid': result['avoid'],
            'watch': result['watch'],
            'report_text': result['report_text'],
        }, f, ensure_ascii=False, indent=2)
    print('Saved: %s' % out_path)

    return result


if __name__ == '__main__':
    result = main()
