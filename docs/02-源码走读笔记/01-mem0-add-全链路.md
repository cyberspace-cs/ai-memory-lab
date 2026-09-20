# 源码走读 01 · mem0 `add()` 全链路（对照 nano_memory）

> 对象：cyberspace-cs/mem0 @ main（2026-09-20 clone，3868 行 main.py）
> 入口：`mem0/memory/main.py` `add()` L760 → `_add_to_vector_store()` L879
> 结论先行：**mem0 主干已从「两阶段 LLM 裁决」演进为「V3 单次 additive 管线」**——与我们把 LLM 裁决降级的设计方向一致，且给出了三个可以抄的工程细节。

## 1. 调用路径（OSS 版，同步）

```
Memory.add(messages, user_id=...)          # L760：scope 校验（user/agent/run 三选一）
  └─ _add_to_vector_store()                # L879
       ├─ infer=False → 原文直接 embedding 入库（跳过抽取）
       └─ infer=True  → V3 PHASED BATCH PIPELINE：
            Phase 0  上下文收集：last 10 messages（代词消解用）
            Phase 1  旧记忆检索：向量 top_k=10
                     ⭐ UUID→序号映射（anti-hallucination：LLM 只能输出 0..n 引用）
            Phase 2  单次 LLM 抽取（ADDITIVE_EXTRACTION_PROMPT）
                     —— 只产 ADD + linked_memory_ids，不产 UPDATE/DELETE
            Phase 3  批量 embedding（失败逐条回退）
            Phase 4  逐条 CPU 处理
            Phase 5  ⭐ MD5 哈希去重（库内 + 批内 seen_hashes 双重）
            Phase 6  批量持久化（失败逐条回退）+ 批量 history 表
            Phase 7  实体链接：抽实体 → 归一 → 批量 embed →
                     精确匹配 or 语义≥0.95 → 合并 linked_memory_ids
```

## 2. prompt 设计要点（ADDITIVE_EXTRACTION_PROMPT, prompts.py L468）

1. **Evidence-bound**：只允许从 New Messages 抽取，Existing Memories 仅用于去重/链接
2. **双角色抽取**：user 的话出个人事实，assistant 的话出"建议/计划"类记忆并显式归因（attributed_to）
3. **反例清单**：明确不抽模糊评价、客套话、assistant 自述能力
4. **时间锚定**：Observation Date（对话发生时）vs Current Date（系统今天）分离——"上周去巴黎"必须落成绝对日期。⭐ 这是双时态的业务轴来源
5. **linked_memory_ids**：新记忆主动挂接旧记忆 UUID——图的雏形，但不做真推理

## 3. 经典两阶段去哪了？

- `DEFAULT_UPDATE_MEMORY_PROMPT`（prompts.py L176）仍在：单次调用让 LLM 对每个候选输出 `ADD/UPDATE/DELETE/NONE + old_memory`——但已不在 `add()` 主路径
- `_update_memory()`（L2038）是显式更新 API
- **解读**：mem0 实测发现"每条候选都拉一次 LLM 裁决"成本/延迟不可接受，改成一次调用 + 确定性规则（哈希、链接）承担大部分冲突处理。**与我们的四档规则优先 + DEFER_LLM 兜底殊途同归**。

## 4. 对照表（mem0 V3 vs nano_memory v0）

| 关注点 | mem0 V3 | nano_memory | 差距/动作 |
|---|---|---|---|
| 去重 | MD5 精确 + LLM 语义跳过 | canonical_text 相等 → NOOP | 加 embedding 相似去重（v2） |
| 更新裁决 | 移出主路径（显式 API） | Resolver 四档规则 | ✅ 我们更结构化 |
| 时间语义 | Observation Date 绝对化 | valid_from/transacted_at 双时态 | ✅ 双时态更完整 |
| 历史表 | db.batch_add_history | ResolutionLog + 永续 item | ✅ 我们带证据链 |
| 实体链接 | 批量 embed + 0.95 合并 | 归一化 + 别名表 + NetworkX | 语义合并（v2） |
| 批处理 | 全程 batch + 逐条回退 | 单条处理 | 上量后补 |

## 5. 三个可直接抄进 nano_memory 的点

1. **UUID→序号映射**防幻觉：LLM 引用旧记忆只允许输出索引号（judge prompt 可加）
2. **Observation Date / Current Date 分离**：extract prompt 模板补这两个字段
3. **批内 seen_hashes**：同一 episode 内部的去重（我们目前只有跨 episode）
