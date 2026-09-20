# ai-memory-lab · AI 记忆与知识工程 DIY 全链路

> 学 mem0 / graphiti / cognee 的设计 → nano-memory 手写全链路 → 独立设计自己的记忆系统。
> 调研报告：[docs/01-开源项目调研.md](docs/01-开源项目调研.md)

## 状态表

| 阶段 | 内容 | 状态 |
|---|---|---|
| P0 | 调研 + fork（mem0/graphiti/cognee/LightRAG/letta） | ✅ 2026-09-20 fork 完成 |
| P1 | 源码走读：mem0 两阶段 → graphiti 双时态 → cognee ECL | ⬜ |
| P2 | nano-memory 全链路实操（6 模块 ~1000 行） | 🟢 v0 可跑：10 测试全绿（NOOP/SUPERSEDE/DEFER/复活复核/溯源/历史查询） |
| P3 | 独立设计 v0 + AuditScope 事件单评测集 | ⬜ |
| P4 | 对比博客 +（可选）开源 | ⬜ |

## 目录

```
docs/
  01-开源项目调研.md      # 六维能力 × 项目对比 + 走读地图
  02-源码走读笔记/        # P1 产出（每项目一份）
  03-独立设计方案-v0.md   # P3 产出
nano_memory/             # P2 手写全链路（六模块对应六能力）
tests/                   # 冲突反转语料 + 溯源验收用例
```

## 六维能力 → 模块映射

| 能力 | 模块 | 抄谁 |
|---|---|---|
| 信息提取 | `extract.py` | mem0 两阶段 prompt |
| 知识结构化 | `schema.py` | letta 分层 + cognee 本体 |
| 实体关系建模 | `graph.py` | graphiti 实体去重 |
| 冲突消解 | `resolve.py` | graphiti 双时态边失效 |
| 来源追溯 | `provenance.py` | graphiti episodic 节点 |
| 动态更新 | `lifecycle.py` | graphiti 增量 + A-mem 演化 |
| （检索） | `retrieve.py` | hybrid + rerank |
