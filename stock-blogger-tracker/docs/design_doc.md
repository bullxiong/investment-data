# 股票博主观点追踪系统 · 设计文档

> 2026-06-29

## 架构图

```
数据采集层              分析引擎层              输出层
┌──────────────┐     ┌─────────────────┐     ┌──────────────┐
│ 雪球(WAF API)│────▶│ SectorExtractor  │     │ daily_brief  │
│ 知乎(API)    │     │  (概念提取+质量) │     │ (日报+叙事)  │
│ ZSXQ(API)×3  │     └───────┬─────────┘     │ dashboard    │
│ 微信(转发)   │             │               │ (5Tab可视化) │
└──────────────┘     ┌───────▼─────────┐     │ SQLite API   │
       │             │ConceptSentiment │     └──────────────┘
       ▼             │ (DeepSeek四维)  │
  TextCleaner         └───────┬─────────┘
  (噪声+alias)               │
                   ┌─────────▼───────────┐
                   │     consensus      │
                   │ (共振+分歧+概念股) │
                   └─────────┬──────────┘
                             │
                   ┌─────────▼───────────┐
                   │  change_detector   │
                   │ (5类变化+调仓信号) │
                   └────────────────────┘
```

## 关键设计决策

### 1. Prompt as Code
所有 LLM prompt 外提为独立文件，通过 `prompt_registry.py` 版本化管理。支持实验新版本、git diff 对比、一键回滚。

### 2. 情感四维标签
从单一 bullish/bearish/neutral 升级为 `sentiment|conviction|time_horizon|risk_acknowledged`。解决"短期谨慎中长期看好"这类 nuance 的判断。

### 3. 概念-股票双重验证
`concept_stocks` 的 overlap 必须同时满足"博主提及 + 概念白名单确认"，防止噪音关联。

### 4. 心跳+定时分离
爬取走心跳（有桌面session→TokenCycler可用），分析走 cron（纯Python，无浏览器依赖）。

### 5. WAF Token 循环
雪球 API 需要 md5__1038 token，单次单页单用户有效。TokenCycler 逐页通过 autoglm 浏览器生成 token，实现全自动循环。

### 6. 上下文质量分类
SectorExtractor 对概念词判断 context_quality（primary/active/passive），passive 顺带提及不参与共振计算。

## 数据流

```
爬虫 → TextCleaner(resolve aliases) → SectorExtractor(context_quality)
  → ConceptSentiment(sent|conv|horizon|risk) → view_timeline
  → consensus(build_resonance + concept_stocks) → resonance.json
  → change_detector → changes_log.json
  → daily_brief + NarrativeEngine → 日报
```

## 调度设计

| 触发 | 内容 | 依赖 |
|------|------|------|
| 心跳(每30min) | 爬取雪球/知乎/ZSXQ | Chrome + 桌面session |
| cron 19:00 | 仪表盘刷新 | 数据文件 |
| cron 20:00 | consensus→change→daily | 数据文件 |
| cron 17:30 | 每日博主复盘 | 数据文件 + DeepSeek |

## 扩展性

- **新博主**: bloggers.json 添加 uid/group_id，orchestrator 自动路由
- **新平台**: 实现 platform_handler，注册到 orchestrator._handlers
- **新prompt**: 放入 prompts/ 目录，register 到 prompt_registry
- **新信号类型**: 扩展 change_detector.SIGNAL_TYPES + daily_brief.TYPE_ORDER

## 技术债务

1. 热力图颜色单一是SVG渲染维度太少 — 需增加渐变/多色映射
2. 微信自动化缺失 — 当前依赖用户转发
3. 概念本体/产业链图谱 — 下一迭代
4. 仪表盘数据嵌入方式 — 当前全量嵌入JSON导致HTML过大
