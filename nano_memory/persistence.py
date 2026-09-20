"""v2 持久化：SQLite 快照【零依赖，stdlib sqlite3】
save_engine / load_engine：全量往返（episodes + items + resolution logs + 图重建）。
设计取舍：
- 图不单独持久化，从 items 的三元组重建（别名表不持久化，构造时再传）
- ResolutionLog 持久化保证审计链跨进程可查
"""
from __future__ import annotations

import json
import sqlite3
from typing import Optional

from .lifecycle import MemEngine
from .schema import (
    Episode,
    MemoryItem,
    MemoryType,
    ResolutionLog,
    SourceRef,
    Status,
)

_SCHEMA = """
CREATE TABLE IF NOT EXISTS episodes (
  id TEXT PRIMARY KEY, text TEXT, ts TEXT, origin TEXT);
CREATE TABLE IF NOT EXISTS items (
  id TEXT PRIMARY KEY, memory_type TEXT, content TEXT,
  subject TEXT, predicate TEXT, object TEXT,
  entities TEXT, valid_from TEXT, valid_to TEXT, invalidated_at TEXT,
  qualifier TEXT, transacted_at TEXT,
  src_episode TEXT, src_ts TEXT, src_origin TEXT,
  confidence REAL, status TEXT, corroborated_by TEXT);
CREATE TABLE IF NOT EXISTS resolutions (
  id TEXT PRIMARY KEY, item_id TEXT, new_item_id TEXT,
  op TEXT, evidence TEXT, judge TEXT, ts TEXT);
"""


def save_engine(engine: MemEngine, path: str) -> None:
    db = sqlite3.connect(path)
    try:
        db.executescript(_SCHEMA)
        db.execute("DELETE FROM episodes")
        db.execute("DELETE FROM items")
        db.execute("DELETE FROM resolutions")
        for ep in engine.episodes.all():
            db.execute("INSERT INTO episodes VALUES (?,?,?,?)",
                       (ep.id, ep.text, ep.ts, ep.origin))
        for it in engine.store.items_including_history():
            db.execute(
                "INSERT INTO items VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (it.id, it.memory_type.value, it.content,
                 it.subject, it.predicate, it.object,
                 json.dumps(it.entities, ensure_ascii=False),
                 it.valid_from, it.valid_to, it.invalidated_at,
                 it.qualifier, it.transacted_at,
                 it.source.episode_id, it.source.ts, it.source.origin,
                 it.confidence, it.status.value,
                 json.dumps(it.corroborated_by, ensure_ascii=False)))
        for log in engine.export_audit()["resolutions"]:
            db.execute("INSERT INTO resolutions VALUES (?,?,?,?,?,?,?)",
                       (log["id"], log["item_id"], log["new_item_id"] or "",
                        log["op"], log["evidence"], log["judge"], log["ts"]))
        db.commit()
    finally:
        db.close()


def load_engine(path: str, **engine_kwargs) -> Optional[MemEngine]:
    """从 SQLite 快照重建 MemEngine。engine_kwargs 透传 extractor/judge/本体声明。"""
    import os
    if not os.path.exists(path):
        return None
    db = sqlite3.connect(path)
    db.row_factory = sqlite3.Row
    try:
        eng = MemEngine(**engine_kwargs)
        for r in db.execute("SELECT * FROM episodes"):
            ep = Episode(text=r["text"], ts=r["ts"], origin=r["origin"], id=r["id"])
            eng.episodes._episodes[ep.id] = ep          # 保 id 持久化
        for r in db.execute("SELECT * FROM items"):
            item = MemoryItem(
                memory_type=MemoryType(r["memory_type"]), content=r["content"],
                subject=r["subject"], predicate=r["predicate"], object=r["object"],
                entities=json.loads(r["entities"]),
                valid_from=r["valid_from"], valid_to=r["valid_to"],
                invalidated_at=r["invalidated_at"], qualifier=r["qualifier"],
                transacted_at=r["transacted_at"],
                source=SourceRef(episode_id=r["src_episode"], ts=r["src_ts"],
                                 origin=r["src_origin"]),
                confidence=r["confidence"], status=Status(r["status"]),
                corroborated_by=json.loads(r["corroborated_by"]),
                id=r["id"])
            eng.store.put(item)
            if item.status in (Status.CURRENT, Status.PENDING):
                eng.graph.add_triple(item.subject, item.predicate, item.object)
        for r in db.execute("SELECT * FROM resolutions ORDER BY ts"):
            eng._logs.append(ResolutionLog(
                item_id=r["item_id"], new_item_id=r["new_item_id"] or None,
                op=r["op"], evidence=r["evidence"], judge=r["judge"], ts=r["ts"],
                id=r["id"]))
        return eng
    finally:
        db.close()
