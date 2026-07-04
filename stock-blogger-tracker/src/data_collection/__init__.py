"""
3AI 协作投资系统 — 数据采集模块
对外接口: collect_articles() + extract_signals()
内部适配各平台爬虫/提取器
"""
import sys
import os

PROJECT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if PROJECT not in sys.path:
    sys.path.insert(0, PROJECT)

from .xueqiu import collect_xueqiu
from .wechat import collect_wechat, collect_zhihu, collect_zsxq, collect_fuli
from .extractor import extract_signals

__all__ = ['collect_articles', 'extract_signals']


def collect_articles(sources, target_date, max_per_source=50):
    """采集原始文章

    见 main.py step_collect() 注释

    Args:
        sources: List[str]  数据源: 'xueqiu'|'zhihu'|'zsxq'|'wechat'|'fuli'
        target_date: str    目标日期 'YYYY-MM-DD'
        max_per_source: int 每个数据源最多收集条数

    Returns:
        List[Dict]  标准化文章列表
    """
    config = _build_config(target_date, max_per_source)

    results = []
    for source in sources:
        collector = _DISPATCH.get(source)
        if collector is None:
            print(f"  [skip] unknown source: {source}")
            continue
        try:
            items = collector(config)
            results.extend(items)
        except Exception as e:
            print(f"  [err] {source}: {e}")

    return results


# ─── 内部调度 ───────────────────────────────────────────

def _build_config(target_date, max_per_source):
    """从环境构建各平台配置"""
    return {
        'target_date': target_date,
        'max_per_source': max_per_source,
        # 雪球
        'uids': ['7251377368', '1034624503'],
        # 知乎
        'zhihu_slugs': ['xiao-peng-61-47'],
        # 知识星球
        'zsxq_groups': ['48888522142558', '51115848448254', '28888221524121'],
        # 复利笔记 — target_date 就是持仓日期
        'hold_date': target_date,
    }


_DISPATCH = {
    'xueqiu': collect_xueqiu,
    'zhihu':  collect_zhihu,
    'zsxq':   collect_zsxq,
    'wechat': collect_wechat,
    'fuli':   collect_fuli,
}
