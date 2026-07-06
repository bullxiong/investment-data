from temporal_engine.sector_levels import get_approaching

def report_for_llm():
    rows = get_approaching(days=14)
    if not rows:
        return '今日无板块接近关键点位。'

    lines = ['## 板块关键点位监测', '']
    triggered = [r for r in rows if r['status'] == 'triggered']
    approaching = [r for r in rows if r['status'] == 'approaching']

    if triggered:
        lines.append('### 已触发 (接近关键位 <1%)')
        for r in triggered[:8]:
            d = '支撑位' if r['signal_type']=='support' else '压力位' if r['signal_type']=='resistance' else '目标位'
            lines.append('- **%s**: %s @%.0f, 当前%.0f (距%.1f%%). %s' % (
                r['sector_name'], d, r['index_level'], r['current_level'],
                r['proximity_pct'], r.get('original_text','')[:80]))
        lines.append('')

    if approaching:
        lines.append('### 接近中 (距关键位 1-5%)')
        for r in approaching[:10]:
            d = '支撑位' if r['signal_type']=='support' else '压力位' if r['signal_type']=='resistance' else '目标位'
            lines.append('- %s: %s @%.0f, 当前%.0f (距%.1f%%). %s' % (
                r['sector_name'], d, r['index_level'], r['current_level'],
                r['proximity_pct'], r.get('original_text','')[:60]))
        lines.append('')

    return '\n'.join(lines)
