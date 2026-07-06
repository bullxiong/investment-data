# TemporalEngine — 时序信号引擎

## PRD v1.0

**项目编号**: stock-blogger-tracker/temporal_engine  
**创建日期**: 2026-07-06  
**状态**: 设计中  
**依赖**: stock-blogger-tracker (数据采集) + SparkSignal (云起信号, 可选)

---

## 1. 产品定位

在现有博主追踪系统（横截面分析）之上，增加**时序维度**的能力层。不修改现有代码，作为独立子项目运行。

**核心价值**: 从"今天谁说了什么" → "信号在时间轴上如何演化" → "当前处于哪个阶段, 应该怎么操作"

## 2. 功能模块

### M1: 信号时间线 (Signal Timeline)

**目标**: 每天爬取完成后, 将 extract_signals() 的输出追加到时间线数据库

**输入**: `extract_signals(articles)` → List[Dict] (8字段信号)  
**输出**: SQLite 表 `signal_timeline`, 每行一条信号 + 日期  
**Schema**:
```sql
CREATE TABLE signal_timeline (
    id INTEGER PRIMARY KEY,
    date TEXT NOT NULL,           -- 'YYYY-MM-DD'
    concept TEXT NOT NULL,        -- '半导体'
    source TEXT NOT NULL,         -- 'xueqiu'/'zhihu'/'zsxq'
    author TEXT,                  -- '白河愁博士'
    author_id TEXT,
    sentiment TEXT,               -- 'bullish'/'bearish'/'neutral'
    direction TEXT,
    confidence REAL,
    article_count INTEGER,        -- 当天该作者该概念的帖子数
    UNIQUE(date, concept, author_id)
);
```

### M2: 板块状态机 (Sector State Machine)

**目标**: 根据 signal_timeline 自动判定每个板块处于哪个生命周期阶段

**状态定义**:
| 阶段 | 判定条件 | 含义 |
|------|----------|------|
| 休眠 | 当前及前3天, 0人提及 | 无人关注 |
| 萌芽 | 当前及前3天, 1-2人提及, 方向未定 | 开始有人提, 可能在酝酿 |
| 共识 | 近3天≥3人提及, 多数同方向, 趋势稳定 | 共识形成中 |
| 确认 | 共识期 + 云起老师给出具体价位信号 | 双重确认 |
| 过热 | 全员看多(≥4人) + 散户持仓集中度上升 | 警惕追高 |
| 退潮 | 方向转向次/人数下降 + 情绪由多转空 | 该撤离了 |

**状态转换图**:
```
休眠 ←→ 萌芽 → 共识 → 确认 → (可能过热) → 退潮 → 休眠
```

**输出格式**:
```python
{
    "概念": "证券",
    "状态": "确认",
    "活跃博主数": 4,
    "主导情绪": "bullish",
    "共识建立天数": 6,
    "云起确认": True,
    "操作建议": "正常仓位",
    "风险提示": None,
}
```

### M3: 共识速度检测 (Consensus Speed)

**目标**: 区分"慢共识"（健康）和"爆款共识"（羊群风险）

**指标**:
- `共识建立天数`: 从第一次提及到达成3人共识的天数
- `加速率`: 近3天新增看多人数 / 前3天新增看多人数

**判定规则**:
| 类型 | 建立天数 | 加速率 | 可信度 | 操作 |
|------|----------|--------|--------|------|
| 慢共识 | ≥5天 | <2x | 高 | 正常参与 |
| 快共识 | 3-5天 | 1-3x | 中 | 轻仓试探 |
| 爆款 | ≤2天 | >3x | 低 | 观望/回避 |

### M4: 时序交叉验证 (Temporal Cross-Verification)

**目标**: 自动检测"宏观先于微观"等时序交叉模式

**模式清单**:
| 编号 | 模式名 | 检测逻辑 | 含义 | 操作 |
|------|--------|----------|------|------|
| CV-1 | 先宏后微 | 博主覆盖≥3人看多 → 随后云起给B/C信号 | 云起确认趋势 | ⭐⭐⭐ 正常仓位 |
| CV-2 | 先微后宏 | 云起先给信号 → 随后博主覆盖增加 | 趋势传播中 | ⭐⭐ 半仓 |
| CV-3 | 仅有微观 | 云起给信号但博主无人覆盖 | 冷门机会 | ⭐ 轻仓 |
| CV-4 | 仅有宏观 | 博主看多但云起无信号 | 缺精确价位 | 📋 观察 |
| CV-5 | 宏观转弱 | 博主从共识→分歧, 云起信号仍有效 | 热度消退 | ⚠️ 减仓 |
| CV-6 | 双弱 | 博主看空 + 云起回避 | 明确看空 | ⛔ 不做 |

### M5: 时序选股漏斗 (Daily Funnel)

**目标**: 每天早上跑一次, 输出排序后的操作候选

**五层过滤**:
```
Layer 1: 活跃信号收集 → 50-80只
Layer 2: 阶段过滤 → 保留共识/确认/萌芽, 剔除休眠/退潮/过热 → 20-30只
Layer 3: 交叉验证 → 保留CV-1/2/3/4, 降级CV-5, 剔除CV-6 → 8-12只
Layer 4: 共识质量排序 → 慢共识 > 快共识 > 爆款 → 保留Top 8
Layer 5: 输出操作清单 + 操作建议文字
```

---

## 3. 技术架构

```
stock-blogger-tracker/
├── temporal_engine/              ← 新子项目
│   ├── __init__.py
│   ├── db.py                     ← SQLite 管理
│   ├── signal_timeline.py        ← M1: 时间线追加
│   ├── sector_state.py           ← M2: 板块状态机
│   ├── consensus_speed.py        ← M3: 共识速度
│   ├── cross_verify.py           ← M4: 时序交叉验证
│   ├── daily_funnel.py           ← M5: 选股漏斗
│   ├── test/
│   │   ├── __init__.py
│   │   ├── seed_data.py          ← 生成模拟数据
│   │   ├── test_timeline.py
│   │   ├── test_sector_state.py
│   │   ├── test_consensus_speed.py
│   │   ├── test_cross_verify.py
│   │   └── test_funnel.py
│   └── data/                     ← 时序DB存放
│       └── temporal.db
```

**数据流**:
```
stock-blogger-tracker daily crawl
        │
        ▼
extract_signals() → List[Dict]
        │
        ▼
temporal_engine.signal_timeline.ingest()  ← 追加到 temporal.db
        │
        ▼
temporal_engine.daily_funnel.run()        ← 跑漏斗
        │
        ▼
输出: JSON + 文本报告
```

**不修改现有代码原则**: temporal_engine 只读取 stock-blogger-tracker 的输出, 不修改其任何文件。通过 import 或 subprocess 调用现有模块。

---

## 4. 测试方案

### 测试数据
`test/seed_data.py` 生成 10 天 × 5 博主 × 5 概念的模拟信号, 覆盖:
- 慢共识场景 (证券)
- 快共识场景 (AI应用)
- 先宏后微场景 (半导体)
- 仅有宏观场景 (新能源)
- 退潮场景 (通信设备)

### 测试用例
| 模块 | 测试点 | 预期 |
|------|--------|------|
| signal_timeline | ingest() 追加数据 | 10天数据, 无重复, 日期正确 |
| signal_timeline | ingest() 重复追加 | 幂等, 不产生重复行 |
| sector_state | 证券板块判定 | "确认" (6天慢共识) |
| sector_state | 通信设备判定 | "退潮" (转空) |
| consensus_speed | 证券板块速度 | "慢共识, 建立天数6" |
| consensus_speed | AI应用速度 | "爆款, 建立天数1" |
| cross_verify | 半导体模式 | CV-1 "先宏后微" |
| cross_verify | 新能源模式 | CV-4 "仅有宏观" |
| daily_funnel | 完整运行 | 输出JSON, 证券排第一, 通信设备被过滤 |

### 验证命令
```bash
# 生成测试数据
python -m temporal_engine.test.seed_data

# 运行各模块测试
python -m temporal_engine.test.test_timeline
python -m temporal_engine.test.test_sector_state
python -m temporal_engine.test.test_consensus_speed
python -m temporal_engine.test.test_cross_verify
python -m temporal_engine.test.test_funnel

# 一键全测
python -m temporal_engine.test.run_all
```

---

## 5. 里程碑

| 里程碑 | 内容 | 验收标准 |
|--------|------|----------|
| M1 | db.py + signal_timeline.py + seed_data.py | ingest() 正确追加, 幂等 |
| M2 | sector_state.py | 10天数据中正确判定5个板块状态 |
| M3 | consensus_speed.py | 正确区分慢/快/爆款共识 |
| M4 | cross_verify.py | 正确检测6种交叉验证模式 |
| M5 | daily_funnel.py | 完整漏斗输出JSON + 文本报告 |

---

## 6. 后续扩展（不在此PRD范围）

- 胜率追踪矩阵 (P1, 需积累2-4周真实数据)
- 实时监控集成 (对接SparkSignal MVP)
- 决策引擎对接 (对接investment-data/src/decision_engine)
- 可视化看板 (HTML交互页面)
