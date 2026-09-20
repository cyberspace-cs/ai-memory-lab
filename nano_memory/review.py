"""DEFER 队列管理：pending 事实的复核出口。
PENDING 事实不入 current 检索池，但持久在库——这里提供复核 API：
  list_pending()                  -> 待复核条目 + 各自的冲突对象
  resolve_pending(decision_fn)    -> 人工/LLM 逐条裁决后转正或否决
decision_fn(pending: MemoryItem, conflicts: list[MemoryItem]) -> (op, evidence)
  op ∈ {ADD, SUPERSEDE, REJECT}；SUPERSEDE 要求 conflicts 非空并顶掉其第一条。
"""
from __future__ import annotations

import os
from typing import Callable, Optional

from .schema import MemoryItem, ResolutionLog, Status

DecisionFn = Callable[[MemoryItem, list[MemoryItem]], tuple[str, str]]


def list_pending(engine) -> list[tuple[MemoryItem, list[MemoryItem]]]:
    """待复核条目及其冲突对象（供人工/LLM 审阅的完整上下文）。"""
    out = []
    for it in engine.store.items_including_history():
        if it.status != Status.PENDING:
            continue
        conflicts = engine.store.current_by_key(it.key)
        out.append((it, conflicts))
    return out


def resolve_pending(engine, decision_fn: DecisionFn) -> list[MemoryItem]:
    """逐条复核 PENDING 条目。返回本轮转正（current）的条目。"""
    promoted: list[MemoryItem] = []
    for pending, conflicts in list_pending(engine):
        op, evidence = decision_fn(pending, conflicts)
        if op == "ADD" or (op == "SUPERSEDE" and not conflicts):
            pending.status = Status.CURRENT
            engine.store.put(pending)
            engine.graph.add_triple(pending.subject, pending.predicate, pending.object)
            promoted.append(pending)
            engine._logs.append(ResolutionLog(
                item_id=pending.id, new_item_id=pending.id, op="ADD",
                evidence=f"manual review: {evidence}", judge="manual"))
        elif op == "SUPERSEDE" and conflicts:
            old = conflicts[0]
            pending.status = Status.CURRENT      # 复核转正后才允许顶替
            engine.store.supersede(old, pending)
            engine.graph.add_triple(pending.subject, pending.predicate, pending.object)
            promoted.append(pending)
            engine._logs.append(ResolutionLog(
                item_id=old.id, new_item_id=pending.id, op="SUPERSEDE",
                evidence=f"manual review: {evidence}", judge="manual"))
        elif op == "REJECT":
            pending.status = Status.DELETED
            engine.store.put(pending)
            engine._logs.append(ResolutionLog(
                item_id=pending.id, new_item_id=None, op="NOOP",
                evidence=f"manual review rejected: {evidence}", judge="manual"))
        else:
            engine._logs.append(ResolutionLog(
                item_id=pending.id, new_item_id=None, op="DEFER_LLM",
                evidence=f"review deferred again: {evidence}", judge="manual"))
    return promoted
