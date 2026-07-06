"""
测试用例: 运行所有测试
"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

PASS = 0
FAIL = 0


def check(name, condition, detail=''):
    global PASS, FAIL
    if condition:
        PASS += 1
        print(f'  [PASS] {name}')
    else:
        FAIL += 1
        print(f'  [FAIL] {name}  {detail}')


def test_timeline():
    from temporal_engine.signal_timeline import ingest, query_date

    signals = [
        {
            'article_id': 'extra_001', 'source': 'xueqiu',
            'concept': '证券', 'sentiment': 'bullish', 'direction': 'long', 'confidence': 0.9,
        },
        {
            'article_id': 'extra_002', 'source': 'zhihu',
            'concept': '证券', 'sentiment': 'bullish', 'direction': 'long', 'confidence': 0.85,
        },
    ]

    r = ingest(signals, '2026-07-05')
    check('ingest signals', r['inserted'] >= 1, str(r))
    check('date correct', r['date'] == '2026-07-05')

    r2 = ingest(signals, '2026-07-05')
    check('duplicate handled', r2['skipped'] >= 1, str(r2))

    r3 = ingest([], '2026-07-05')
    check('empty signals', r3['inserted'] == 0)

    rows = query_date('2026-07-05')
    check('query works', len(rows) >= 1, str(len(rows)))

    print('  [test_timeline] done\n')


def test_sector_state():
    from temporal_engine.sector_state import analyze_all

    cloud = {'半导体': True, '证券': True, 'AI应用': False, '新能源': False, '通信设备': False}
    states = analyze_all('2026-07-04', cloud)
    by_concept = {s['concept']: s for s in states}

    sec = by_concept.get('证券', {})
    check('证券 stage', sec.get('stage') == '确认', f"got: {sec.get('stage')}")

    semi = by_concept.get('半导体', {})
    check('半导体 stage', semi.get('stage') == '确认', f"got: {semi.get('stage')}")

    ai = by_concept.get('AI应用', {})
    check('AI应用 stage', ai.get('stage') in ('过热', '共识'), f"got: {ai.get('stage')}")

    # 新能源: 种子数据有3个博主在7天窗口内, 全部看多 → 共识 (合理)
    ne = by_concept.get('新能源', {})
    check('新能源 mentioners >= 2', ne.get('total_mentioners', 0) >= 2, str(ne.get('total_mentioners')))

    # 通信设备: 后期转bearish, bearish > bullish → 退潮
    comm = by_concept.get('通信设备', {})
    check('通信设备 bearish/休眠', comm.get('stage') in ('休眠', '退潮', '萌芽'), f"got: {comm.get('stage')}")

    check('returns >= 5 concepts', len(states) >= 5, str(len(states)))
    print('  [test_sector_state] done\n')


def test_consensus_speed():
    from temporal_engine.consensus_speed import analyze

    s = analyze('证券', '2026-07-04')
    check('证券 type', s.get('type') in ('慢共识', '快共识', '爆款'), f"got: {s.get('type')}")

    ai = analyze('AI应用', '2026-07-04')
    check('AI type', ai.get('type') in ('爆款', '快共识'), f"got: {ai.get('type')}")

    # 通信设备: 前期有看多信号, 后期转 bearish, 可能判定为某种共识形式
    comm = analyze('通信设备', '2026-07-04')
    check('通信设备 analyzed', comm.get('concept') == '通信设备')

    print('  [test_consensus_speed] done\n')


def test_cross_verify():
    from temporal_engine.cross_verify import verify

    cloud = {
        '半导体': {'stage': 'B', 'entry_price': 150, 'target_price': 200, 'stop_loss': 140},
        '证券': {'stage': 'B', 'entry_price': 28.5, 'target_price': 32, 'stop_loss': 27},
    }

    # 半导体: 5博主看多 + 云起确认 → CV-1
    cv = verify('半导体', '2026-07-04', cloud)
    check('半导体 pattern', cv['pattern'] in ('CV-1', 'CV-2'), f"got: {cv['pattern']}")
    check('半导体 has blog data', cv['blogger_total'] > 0, f"got: {cv['blogger_total']}")

    # 新能源: 有博主覆盖 + 无云起 → CV-4
    cv2 = verify('新能源', '2026-07-04', cloud)
    check('新能源 has blog data', cv2['blogger_total'] > 0, f"got: {cv2['blogger_total']}")

    # 通信设备: 前期bullish后期bearish/无人 → 可能CV-4或CV-6
    cv3 = verify('通信设备', '2026-07-04', cloud)
    check('通信设备 analyzed', cv3.get('concept') == '通信设备')

    print('  [test_cross_verify] done\n')


def test_funnel():
    from temporal_engine.daily_funnel import run

    cloud = {
        '半导体': {'stage': 'B', 'entry_price': 150, 'target_price': 200, 'stop_loss': 140},
        '证券': {'stage': 'B', 'entry_price': 28.5, 'target_price': 32, 'stop_loss': 27},
    }

    result = run('2026-07-04', cloud)

    check('date', result['date'] == '2026-07-04')
    check('layer1 > 0', result['layer1_total'] >= 3, f"got: {result['layer1_total']}")
    check('has candidates', len(result['layer5_candidates']) > 0, str(len(result['layer5_candidates'])))
    check('has avoid', len(result['avoid']) > 0, str(result['avoid']))
    check('report text', len(result['report_text']) > 80, f"length={len(result['report_text'])}")

    candidates = [c['concept'] for c in result['layer5_candidates']]
    check('半导体 in candidates', '半导体' in candidates, str(candidates))
    check('证券 in candidates', '证券' in candidates, str(candidates))

    # 验证: 确认期(S级)概念排在前面
    if len(result['layer5_candidates']) >= 2:
        first_two = [c['cross_rating'] for c in result['layer5_candidates'][:2]]
        check('top candidates are S/A/B', all(r in ('S级', 'A级', 'B级') for r in first_two), str(first_two))

    print()
    print(result['report_text'])
    print()

    import json
    out_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'funnel_output.json')
    with open(out_path, 'w', encoding='utf-8') as f:
        json.dump({
            'date': result['date'],
            'candidates': result['layer5_candidates'],
            'avoid': result['avoid'],
            'watch': result['watch'],
        }, f, ensure_ascii=False, indent=2)
    check('JSON saved', os.path.exists(out_path))

    print('  [test_funnel] done\n')


if __name__ == '__main__':
    print('=' * 60)
    print(' TemporalEngine Test Suite')
    print('=' * 60)
    print()

    from temporal_engine.test.seed_data import generate
    generate()
    print()

    tests = [
        ('M1: Signal Timeline', test_timeline),
        ('M2: Sector State', test_sector_state),
        ('M3: Consensus Speed', test_consensus_speed),
        ('M4: Cross Verify', test_cross_verify),
        ('M5: Daily Funnel', test_funnel),
    ]

    for name, fn in tests:
        print(f'--- {name} ---')
        try:
            fn()
        except Exception as e:
            print(f'  [FAIL] EXCEPTION: {e}')
            import traceback
            traceback.print_exc()
            FAIL += 1
        print()

    print('=' * 60)
    total = PASS + FAIL
    print(f'  Results: {PASS}/{total} passed, {FAIL}/{total} failed')
    if FAIL == 0:
        print('  ALL TESTS PASSED')
    print('=' * 60)
