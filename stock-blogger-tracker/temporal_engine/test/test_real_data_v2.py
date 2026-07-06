"""
完整真实数据测试: 真实云起(191条) + 真实博主(cross_signals.json 418条) → 时序漏斗
"""
import sys, os, json

STB = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, STB)

from temporal_engine.db import init_db, get_conn
from temporal_engine.cloud_adapter import load_cloud_signals
from temporal_engine.blogger_loader import ingest_all
from temporal_engine.daily_funnel import run as run_funnel


def main():
    print('=' * 60)
    print(' REAL DATA TEST v2: Real Cloud + Real Blogger')
    print('=' * 60)
    print()

    # Step 1: 加载真实云起数据 (v2: sw2_industry_map)
    print('[1] Loading cloud signals (v2: sw2_industry_map)...')
    cloud = load_cloud_signals()
    concepts_cloud = list(cloud.keys())
    print('    Cloud concepts (%d): %s' % (len(concepts_cloud), concepts_cloud))
    print()

    # Step 2: 注入真实博主数据
    print('[2] Loading real blogger data...')
    init_db()
    conn = get_conn()
    conn.execute("DELETE FROM signal_timeline")
    conn.commit()
    conn.close()
    ingest_all('2026-06-28')
    print()

    # Step 3: 查看博主覆盖的概念
    conn = get_conn()
    rows = conn.execute(
        "SELECT concept, COUNT(*) as cnt, sentiment FROM signal_timeline GROUP BY concept, sentiment ORDER BY cnt DESC"
    ).fetchall()
    blogger_concepts = {}
    for r in rows:
        c = r['concept']
        if c not in blogger_concepts:
            blogger_concepts[c] = {'bullish': 0, 'bearish': 0, 'neutral': 0, 'total': 0}
        blogger_concepts[c][r['sentiment']] = r['cnt']
        blogger_concepts[c]['total'] += r['cnt']
    conn.close()

    print('[3] Blogger concept coverage:')
    for c in sorted(blogger_concepts.keys(), key=lambda x: -blogger_concepts[x]['total']):
        bc = blogger_concepts[c]
        print('    %-20s total=%3d bullish=%3d bearish=%3d' %
              (c, bc['total'], bc['bullish'], bc['bearish']))

    # 重叠分析
    overlap = set(concepts_cloud) & set(blogger_concepts.keys())
    cloud_only = set(concepts_cloud) - set(blogger_concepts.keys())
    blogger_only = set(blogger_concepts.keys()) - set(concepts_cloud)
    print()
    print('    [RESONANCE] Overlap (%d): %s' % (len(overlap), list(overlap)))
    print('    Cloud-only (%d): %s' % (len(cloud_only), list(cloud_only)))
    print('    Blogger-only (%d): %s' % (len(blogger_only), list(blogger_only)))
    print()

    # Step 4: 跑漏斗
    print('[4] Running funnel...')
    result = run_funnel('2026-06-28', cloud)
    print()

    # Step 5: 输出
    print('=' * 60)
    print(' RESULTS')
    print('=' * 60)
    print('Layer 1 (active): %d' % result['layer1_total'])
    print('Layer 2 (filtered): %d' % result['layer2_after_filter'])
    print('Layer 5 (candidates): %d' % len(result['layer5_candidates']))
    print()
    print('Avoid (%d): %s' % (len(result['avoid']), result['avoid']))
    print('Watch (%d): %s' % (len(result['watch']), result['watch']))
    print()
    print(result['report_text'])
    print()

    # Step 6: 保存
    out_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'real_data_v2_output.json')
    with open(out_path, 'w', encoding='utf-8') as f:
        json.dump({
            'date': result['date'],
            'overlap': list(overlap),
            'cloud_only': list(cloud_only),
            'blogger_only': list(blogger_only),
            'candidates': result['layer5_candidates'],
            'avoid': result['avoid'],
            'watch': result['watch'],
            'report_text': result['report_text'],
        }, f, ensure_ascii=False, indent=2)
    print('Saved: %s' % out_path)

    return result


if __name__ == '__main__':
    main()
