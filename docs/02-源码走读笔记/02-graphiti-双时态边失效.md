# 源码走读 02 · graphiti `add_episode` 双时态与边失效（对照 nano_memory）

> 对象：cyberspace-cs/graphiti @ main（2026-09-20 clone）
> 入口：`graphiti_core/graphiti.py` `add_episode()` L1043 → `_resolve_edges()` L689
> 核心：`graphiti_core/utils/maintenance/edge_operations.py` `resolve_extracted_edges()` L325
> 结论先行：**双时态的灵魂在一行代码**——旧边 `invalid_at = 新边.valid_at`（业务轴失效）+ `expired_at = now`（事务轴失效），两个时间轴分离使"回到过去查真相"成为可能。

## 1. 边解析全流程

```
resolve_extracted_edges(extracted_edges, episode, entities)
  ├─ 候选分两池：
  │   ├─ duplicate 候选：同 (source, target) 端点的边
  │   └─ invalidation 候选：更广的相关边（去重后）
  ├─ 每条 extracted edge 并发调 resolve_extracted_edge()：
  │   ├─ 连续索引上下文（invalidation 候选 idx 接在 duplicate 之后）⭐ 防幻觉，同 mem0
  │   ├─ LLM 小模型出 EdgeDuplicate{duplicate_facts: [idx]}（结构化输出）
  │   ├─ 命中 duplicate → resolved = 旧边本体，episode.uuid 追加进旧边
  │   │   ⭐ 重复不丢弃 = 溯源累积：一条边被多少 episode 印证可直接数
  │   └─ 矛盾 → 进 invalidated 流程
  └─ 返回 (resolved_edges, invalidated_edges, new_edges)
```

## 2. 双时态失效的那一行（L548-570, edge_operations.py）

```python
edge.invalid_at = resolved_edge.valid_at      # 业务轴：从新事实生效那刻起，旧事实不再为真
edge.expired_at = edge.expired_at or utc_now() # 事务轴：系统此刻知道它失效了
```

- `valid_at / invalid_at`：**事实在现实世界中的成立区间**（业务时间）
- `expired_at`：**系统何时学到它失效**（事务时间）
- 二者分离 → 可回答两类问题：*现在什么是真的*（invalid_at 过滤）与 *9 月 15 日当时系统认为什么是真的*（expired_at 过滤）——后者正是审计/事故复盘/RFT 标注需要的
- 边还有 `extract_valid_invalid_dates()` 轻量 LLM 调用：从文本抽业务时间，抽不出则用 episode 时间兜底

## 3. 对照 nano_memory（resolve.py / schema.py）

| 关注点 | graphiti | nano_memory v0 | 评估 |
|---|---|---|---|
| 时间轴 | valid_at/invalid_at + expired_at | valid_from/valid_to + transacted_at | ✅ 等价分离，但旧条未记"何时被失效"——ResolutionLog 有，item 本身没有 |
| 重复处理 | **重用旧边 + episodes 追加**（溯源累积） | NOOP → **corroborated_by 累积 + confidence+0.05**（本轮已抄） | ✅ |
| 矛盾判定 | LLM 判 duplicate/contradiction（小模型+结构化） | 规则四档，LLM 只兜底 | ✅ 差异化保留 |
| 失效粒度 | 单边（一条 fact 一条边） | 同键整条 supersede | graphiti 更细 |
| 防幻觉 | 连续索引 + 结构化模型校验 idx 范围 | judge 只回 op/evidence | 可抄 idx 模式 |
| 并发 | semaphore_gather 批量 | 串行 | 上量再改 |

## 4. 本轮已抄进代码（1 个）

**NOOP 印证累积**：重复候选不再空手而归——`matched_item.corroborated_by.append(episode_id)`，confidence +0.05 封顶 1.0。多源印证让"这条事实被说过几次"成为可查信号（检索 rerank 与 RFT 数据质量分都能用）。

## 5. 待抄清单（P2 backlog）

1. **旧条记录失效时刻**：supersede 时在旧 item 上记 `invalidated_at = now`（事务轴显式化，目前只在 ResolutionLog 里）
2. **judge 加连续索引**：LLM 裁决时引用旧条目用 idx 而非自由文本
3. **失效粒度细化**：object 相同但限定条件不同（"周末加班"vs"平时加班"）不应互相顶替 → predicate 细分或 edge condition 字段
