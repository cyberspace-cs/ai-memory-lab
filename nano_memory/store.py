"""存储层：MemoryStore（全量 item，含 superseded，永续可查）+ 索引。
不变量 3：同一 (subject,predicate) 任一时刻最多一条 current —— 由 upsert 保证。
"""
from __future__ import annotations

import json
from typing import Optional

from .schema import MemoryItem, Status


class MemoryStore:
    def __init__(self) -> None:
        self._items: dict[str, MemoryItem] = {}

    # ── 写 ─────────────────────────────────────────────────
    def put(self, item: MemoryItem) -> None:
        self._items[item.id] = item

    def supersede(self, old: MemoryItem, new: MemoryItem) -> None:
        """不变量 2：旧条不删除，只闭区间 + 改状态。
        事务轴显式化【抄 graphiti expired_at】：invalidated_at 记系统何时学到失效。"""
        old.valid_to = new.valid_from or old.valid_to
        old.status = Status.SUPERSEDED
        old.invalidated_at = new.transacted_at
        self._items[old.id] = old
        self._items[new.id] = new

    # ── 读 ─────────────────────────────────────────────────
    def get(self, item_id: str) -> Optional[MemoryItem]:
        return self._items.get(item_id)

    def current_by_key(self, key: tuple[str, str]) -> list[MemoryItem]:
        """同键 current 条目（正常应 ≤1，返回 list 以便测试发现违规）。"""
        return [i for i in self._items.values()
                if i.key == key and i.status == Status.CURRENT]

    def current_items(self, memory_type: Optional[str] = None) -> list[MemoryItem]:
        return [i for i in self._items.values()
                if i.status == Status.CURRENT
                and (memory_type is None or i.memory_type.value == memory_type)]

    def items_including_history(self) -> list[MemoryItem]:
        """⑥ 差异化：superseded 永续可查（'当时的真相'）。"""
        return list(self._items.values())

    def history_of(self, key: tuple[str, str]) -> list[MemoryItem]:
        """一条骨架键的完整演化链（按 valid_from 排序）。"""
        chain = [i for i in self._items.values() if i.key == key]
        return sorted(chain, key=lambda i: (i.valid_from or "", i.transacted_at))

    # ── 持久化（JSON 快照） ────────────────────────────────
    def save(self, path: str) -> None:
        json.dump([i.to_dict() for i in self._items.values()],
                  open(path, "w", encoding="utf-8"), ensure_ascii=False, indent=1)

    def load(self, path: str) -> None:
        for d in json.load(open(path, encoding="utf-8")):
            item = MemoryItem.from_dict(d)
            self._items[item.id] = item
