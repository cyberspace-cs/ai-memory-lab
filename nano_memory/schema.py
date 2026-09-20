"""② 知识结构化【抄 letta 分层 + cognee 本体；对照 fork: letta/schemas/memory.py】
MemEngine §2 核心数据模型：MemoryItem / SourceRef / FactCandidate / ResolutionLog。
"""
from __future__ import annotations

import dataclasses
import enum
import hashlib
import time
import uuid
from dataclasses import dataclass, field
from typing import Optional


class MemoryType(str, enum.Enum):
    EPISODIC = "episodic"      # 事件：某时某地发生了什么
    SEMANTIC = "semantic"      # 事实/概念：世界状态
    PROCEDURAL = "procedural"  # 偏好/流程：用户怎么做事


class Status(str, enum.Enum):
    CURRENT = "current"
    SUPERSEDED = "superseded"
    DELETED = "deleted"
    PENDING = "pending"  # DEFER_LLM 且无 judge 时挂起


@dataclass
class SourceRef:
    """⑤ 来源追溯【抄 graphiti EpisodicNode】：没有来源的事实不允许入库。"""
    episode_id: str
    chunk_idx: int = 0
    ts: Optional[str] = None       # 业务时间（ISO 字符串），双时态的业务轴
    origin: str = "conversation"   # conversation | document | incident | ...

    def to_dict(self) -> dict:
        return dataclasses.asdict(self)


@dataclass
class MemoryItem:
    memory_type: MemoryType
    content: str                              # 人读的事实陈述
    subject: str
    predicate: str
    object: str
    source: SourceRef                         # 不变量 1：强制非空
    entities: list[str] = field(default_factory=list)
    valid_from: Optional[str] = None          # ④ 双时态业务轴
    valid_to: Optional[str] = None
    transacted_at: str = field(default_factory=lambda: _now_iso())
    confidence: float = 1.0
    status: Status = Status.CURRENT
    corroborated_by: list[str] = field(default_factory=list)  # 多源印证的 episode_id（NOOP 时累积）
    id: str = field(default_factory=lambda: uuid.uuid4().hex[:12])

    @property
    def key(self) -> tuple[str, str]:
        """冲突判定的骨架键：(subject, predicate)。不变量 3 基于它。"""
        return (canonical_text(self.subject), canonical_text(self.predicate))

    def to_dict(self) -> dict:
        d = dataclasses.asdict(self)
        d["memory_type"] = self.memory_type.value
        d["status"] = self.status.value
        d["source"] = self.source.to_dict()
        return d

    @classmethod
    def from_dict(cls, d: dict) -> "MemoryItem":
        d = dict(d)
        d["memory_type"] = MemoryType(d["memory_type"])
        d["status"] = Status(d["status"])
        d["source"] = SourceRef(**d["source"])
        return cls(**d)


@dataclass
class FactCandidate:
    """提取器产物：尚未入库的候选事实。"""
    memory_type: MemoryType
    content: str
    subject: str
    predicate: str
    object: str
    valid_from: Optional[str] = None
    confidence: float = 0.8

    def to_item(self, source: SourceRef) -> MemoryItem:
        return MemoryItem(
            memory_type=self.memory_type, content=self.content,
            subject=self.subject, predicate=self.predicate, object=self.object,
            entities=_entities_of(self), valid_from=self.valid_from,
            confidence=self.confidence, source=source,
        )


@dataclass
class ResolutionLog:
    """④ 裁决日志【差异化：裁决是一等公民，evidence 非空（不变量 4）】。"""
    item_id: str                  # 受影响的已有 item（NOOP/DEFER 时为冲突对象）
    new_item_id: Optional[str]    # 新入库的 item（ADD/SUPERSEDE 时）
    op: str                       # ADD | NOOP | SUPERSEDE | KEEP_BOTH | DEFER_LLM
    evidence: str                 # 裁决依据，禁止为空
    judge: str                    # rules | llm | manual
    ts: str = field(default_factory=lambda: _now_iso())
    id: str = field(default_factory=lambda: uuid.uuid4().hex[:12])

    def to_dict(self) -> dict:
        return dataclasses.asdict(self)


@dataclass
class Episode:
    """⑤ 原始内容块【抄 graphiti episode】。"""
    text: str
    ts: str
    origin: str
    id: str = field(default_factory=lambda: uuid.uuid4().hex[:12])


def canonical_text(s: str) -> str:
    return s.strip().lower()


def fact_digest(subject: str, predicate: str, object: str) -> str:
    return hashlib.sha1(
        "|".join(canonical_text(x) for x in (subject, predicate, object)).encode()
    ).hexdigest()[:12]


def _entities_of(c: FactCandidate) -> list[str]:
    seen, out = set(), []
    for x in (c.subject, c.object):
        k = canonical_text(x)
        if k not in seen:
            seen.add(k)
            out.append(x)
    return out


def _now_iso() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime()) + "Z"
