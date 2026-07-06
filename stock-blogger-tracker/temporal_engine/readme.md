# TemporalEngine — 时序信号引擎

在博主追踪系统 + SparkSignal 之上增加时序维度的能力层。

## 快速开始

```bash
# 1. 准备数据文件
cp /path/to/sw2_industry_map.json temporal_engine/data/
cp /path/to/cleaned_system_view_v4.json temporal_engine/data/

# 2. 运行测试
cd stock-blogger-tracker
python temporal_engine/test/run_all.py

# 3. 加载真实数据 + 生成 LLM 日报
python temporal_engine/scripts/ingest_and_report.py --date 2026-07-04
```

## 模块

| 模块 | 文件 | 功能 |
|------|------|------|
| M0 | db.py | SQLite 管理 |
| M1 | signal_timeline.py | 信号时间线存储 |
| M2 | sector_state.py | 板块状态机 (休眠→萌芽→共识→确认→过热→退潮) |
| M3 | consensus_speed.py | 共识速度检测 (慢/快/爆款) |
| M4 | cross_verify.py | 时序交叉验证 (6种模式) |
| M5 | daily_funnel.py | 五层选股漏斗 |
| M6 | sector_levels.py | 板块关键点位追踪 |
| M7 | llm_analyst.py | DeepSeek 日报生成 |
| - | cloud_adapter.py | SparkSignal 数据适配器 |
| - | blogger_loader.py | 博主数据加载器 |

## 数据文件

```
temporal_engine/
├── data/
│   ├── temporal.db          ← 信号时间线 (gitignored)
│   ├── sw2_industry_map.json ← 申万二级行业映射
│   ├── cleaned_system_view_v4.json  ← 云起信号 (gitignored)
│   └── reports/             ← 每日 LLM 报告 (gitignored)
└── prompts/
    └── daily_analyst.txt    ← LLM Prompt 模板
```

## 依赖

- Python 3.10+
- SQLite3 (内置)
- DeepSeek API (用于 M7)
