"""nano-memory：六模块对应六能力的最小记忆系统实现（P2 实操，v0 纯标准库可跑）。

模块 ↔ 能力 ↔ 上游对照（fork 已建：cyberspace-cs/{mem0,graphiti,cognee,LightRAG,letta}）：
  schema.py      ② 知识结构化   letta/schemas/memory.py
  provenance.py  ⑤ 来源追溯     graphiti_core/nodes.py (EpisodicNode)
  extract.py     ① 信息提取     mem0/memory/main.py add() 两阶段
  resolve.py     ④ 冲突消解     graphiti_core/utils/maintenance/edge_operations.py
  graph.py       ③ 实体关系     graphiti add_episode 实体去重
  store.py       存储           ——
  retrieve.py    检索           mem0 hybrid search + graphiti 图距离 rerank
  lifecycle.py   ⑥ 动态更新     graphiti 增量 + A-mem 演化
"""
