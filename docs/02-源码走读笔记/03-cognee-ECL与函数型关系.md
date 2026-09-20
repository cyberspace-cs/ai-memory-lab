# 源码走读 03 · cognee ECL 管线与时间矛盾消解（对照 nano_memory）

> 对象：cyberspace-cs/cognee @ main（2026-09-20 clone）
> 入口：`cognee/api/v1/cognify/cognify.py` `cognify()` L109；矛盾消解：`cognee/tasks/graph/resolve_temporal_contradictions.py`
> 结论先行：cognee 的招牌是 **ECL（Extract→Cognify→Load）任务图 + pydantic 图模式声明 + 可选本体**；其 `resolve_temporal_contradictions`（issue #3631）给出了一条重要设计公理——**函数型关系必须显式声明，不能靠推断**。

## 1. cognify() 参数里藏着的架构决策

| 参数 | 决策 |
|---|---|
| `graph_model: BaseModel = KnowledgeGraph` | **抽取目标是一个 pydantic 模型**——用类型定义"图长什么样"，LLM 按模型结构化输出（默认 `nodes+edges+summary`） |
| `ontology_file_path` | 本体驱动抽取 schema 规划 + 图集成（记忆系统接领域本体的正路） |
| `extractor: "llm" \| "gliner"` | **可换本地小模型抽取**（GLiNER）——成本分层的先例 |
| `temporal_cognify: bool = False` | 时间维度是**可选管线**：事件抽取 + 时间戳 + 矛盾消解 |
| `incremental_loading=True` | 增量是默认值（与 GraphRAG 的全量重建划清界限） |
| `custom_prompt` | 领域定制抽取的官方口子 |

## 2. resolve_temporal_contradictions：三句话精华

1. **只处理调用者声明的 functional relationships**（单值关系，如"公司现任 CEO"）——因为 LLM 抽出的关系（`knows`/`mentions`）大多多值、无基数元数据，**哪些关系单值无法推断，只能声明**（模块 docstring 原话）
2. 消解策略：同主体同函数型关系出现多值 → 保留最近断言，旧的打标 `superseded / superseded_by / supersession_reason`，**物理不删除**（provenance 留在图上）
3. 实体节点 id 确定性（`Entity:<name>`）→ 重新提及的实体沿用旧 id，"今天的事实可以 supersede 上月的"——跨批次消解的基础

## 3. 对照 nano_memory

| 关注点 | cognee | nano_memory | 评估 |
|---|---|---|---|
| 图模式 | pydantic `graph_model` 声明 | 隐式 (s,p,o) 骨架 | v2 加 pydantic schema 声明（运维本体：告警/日志/拓扑） |
| 函数型声明 | **必须显式**，否则不消解 | 默认全部函数型（`functional=None`） | ✅ 已抄：`functional_predicates` 参数，未声明者多值并存 |
| 消解时机 | 图写入后批量回看邻域 | 写入时单条同步 | 时序管线（v2）可再补事后巡检 |
| 旧值处理 | 打标不删（superseded_by/reason） | status=superseded + ResolutionLog | ✅ 等价，我们多一条日志通道 |
| 确定性实体 id | `Entity:<name>` | canonical_text 归一 | ✅ 等价 |
| 本体 | ontology_file_path | 无 | 运维本体时照抄 |

## 4. 本轮已抄进代码（1 个）

**`functional_predicates` 显式声明**：MemEngine 构造参数；未声明的 predicate 视为多值关系，同键不同 object 走 `resolve_multivalue` 并存（仅完全相同内容 NOOP）。测试锁定：非函数型并存 ✅ / 函数型仍 supersede ✅。这也顺手修正了 v0 的一个隐含错误假设——"likes_drink 换饮品"其实是多值偏好，不该默认顶替。

## 5. P1 走读收官小结（mem0 + graphiti + cognee）

| 项目 | 一句话精髓 | 我们抄到代码里的 |
|---|---|---|
| mem0 | 工程化：批处理、哈希去重、链接、history 表 | 观察日期分离（待做）、批内去重（待做） |
| graphiti | 双时态：业务轴/事务轴分离 + 边失效 | NOOP 印证累积 ✅、invalidated_at ✅ |
| cognee | 声明式：函数型关系、本体、pydantic 图模式 | functional_predicates ✅ |
