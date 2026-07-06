"""
每日完整流水线: 数据摄入 → 漏斗 → LLM 日报
用法: python scripts/daily_report.py --date 2026-07-04
"""
import sys, os, argparse, json, shutil

STB = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, STB)

from temporal_engine.db import init_db, get_conn
from temporal_engine.cloud_adapter import load_cloud_signals
from temporal_engine.blogger_loader import ingest_all
from temporal_engine.daily_funnel import run as run_funnel
from temporal_engine.llm_analyst import daily_report
from temporal_engine.sector_levels import seed as seed_sector_levels, update_all_current


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--date', default='2026-07-04', help='Target date YYYY-MM-DD')
    parser.add_argument('--no-llm', action='store_true', help='Skip LLM call')
    parser.add_argument('--sector-levels', type=str, help='JSON of {sector: current_level}')
    args = parser.parse_args()

    target_date = args.date
    print('=' * 60)
    print(' Daily Pipeline: %s' % target_date)
    print('=' * 60)
    print()

    # 0. Init
    init_db()
    print('[0] DB initialized')

    # 1. 加载云起数据
    print('[1] Loading cloud signals...')
    cloud = load_cloud_signals()
    print('    Concepts: %d' % len(cloud))

    # 2. 注入博主数据
    print('[2] Loading blogger data...')
    conn = get_conn()
    conn.execute("DELETE FROM signal_timeline")
    conn.commit()
    conn.close()
    ingest_all(target_date)

    # 3. 板块点位
    print('[3] Sector levels...')
    seed_sector_levels()
    if args.sector_levels:
        levels = json.loads(args.sector_levels)
        update_all_current(levels)
        print('    Updated %d sectors' % len(levels))

    # 4. 监控
    print('[4] Checking signal coverage...')
    conn = get_conn()
    count = conn.execute("SELECT COUNT(*) FROM signal_timeline WHERE date=?", (target_date,)).fetchone()[0]
    concepts = conn.execute("SELECT COUNT(DISTINCT concept) FROM signal_timeline WHERE date=?", (target_date,)).fetchone()[0]
    conn.close()
    print('    %d signals across %d concepts' % (count, concepts))

    # 5. 漏斗
    print('[5] Running funnel...')
    funnel = run_funnel(target_date, cloud)
    print('    %d candidates' % len(funnel['layer5_candidates']))

    # 6. LLM 日报
    if args.no_llm:
        print('[6] LLM skipped (--no-llm)')
        report = {'report_text': funnel.get('report_text', ''), 'success': True}
    else:
        print('[6] Generating LLM report...')
        report = daily_report(target_date, cloud)

    # 7. 输出
    print()
    print('=' * 60)
    print(' RESULT')
    print('=' * 60)
    print('Date: %s' % target_date)
    print('Signals: %d' % count)
    print('Candidates: %d' % len(funnel['layer5_candidates']))
    print('LLM: %s' % ('ok' if report.get('success') else 'failed'))
    print()

    # 8. 保存完整输出
    out_dir = os.path.join(STB, 'temporal_engine', 'data', 'reports')
    os.makedirs(out_dir, exist_ok=True)

    summary = {
        'date': target_date,
        'signal_count': count,
        'concept_count': concepts,
        'funnel_candidates': funnel['layer5_candidates'],
        'funnel_avoid': funnel['avoid'],
        'funnel_watch': funnel['watch'],
        'llm_report_success': report.get('success'),
        'llm_report_path': report.get('path', ''),
    }
    with open(os.path.join(out_dir, 'summary_%s.json' % target_date), 'w', encoding='utf-8') as f:
        json.dump(summary, f, ensure_ascii=False, indent=2)

    print('Report text:')
    print(report.get('report_text', 'N/A')[:1000])
    print('...')
    print()
    print('Done. Full report: %s' % report.get('path', 'N/A'))


if __name__ == '__main__':
    main()
