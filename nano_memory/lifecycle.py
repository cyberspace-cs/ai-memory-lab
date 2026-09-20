"""⑥ 动态更新 + Facade【graphiti 增量插入 + A-mem 演化思想】
MemEngine：add_episode（提取→裁决→入库全链路）/ query / decay / export_audit。
"""
from __future__ import annotations

import time
from typing import Optional

from .extract import BaseExtractor, RuleExtractor
from .graph import EntityGraph
from .provenance import EpisodeStore, validate_source
from .resolve import LLMJudge, Resolution, Resolver
from .retrieve import Answer, Retriever
from .schema import (
    FactCandidate,
    MemoryItem,
    ResolutionLog,
    SourceRef,
    Status,
    canonical_text,
)
from .store import MemoryStore


class MemEngine:
    def __init__(self, extractor: Optional[BaseExtractor] = None,
                 judge: Optional[LLMJudge] = None,
                 graph_aliases: Optional[dict[str, str]] = None,
                 functional_predicates: Optional[set[str]] = None) -> None:
        """functional_predicates【抄 cognee resolve_temporal_contradictions】：
        单值关系需显式声明（如 lives_in/works_at/root_cause），声明了才允许
        SUPERSEDE；未声明的多值关系（如 likes）仅凭同键不做互顶。
        限定条件（qualifier）不同时永远并存，不受此声明影响。"""
        self.functional = functional_predicates  # None = 全部函数型（v0 默认，向后兼容）
        self.episodes = EpisodeStore()
        self.store = MemoryStore()
        self.graph = EntityGraph(aliases=graph_aliases)
        self.resolver = Resolver(judge=judge)
        self.extractor: BaseExtractor = extractor or RuleExtractor()
        self.retriever = Retriever(self.store, self.graph,
                                   source_text_fn=self.episodes.source_text)
        self._logs: list[ResolutionLog] = []

    # ── 写入全链路：episode → 提取 → 裁决 → 入库 ──────────
    def add_episode(self, text: str, ts: Optional[str] = None,
                    origin: str = "conversation") -> list[MemoryItem]:
        ep = self.episodes.add(text, ts=ts, origin=origin)
        candidates = self.extractor.extract(text, ts=ts)
        return self._ingest(candidates, ep.id, origin, default_ts=ts)

    def add_facts(self, facts: list[dict], ts: Optional[str] = None,
                  origin: str = "conversation",
                  episode_text: Optional[str] = None) -> list[MemoryItem]:
        """结构化直通（绕过抽取，仍走裁决+溯源——测试/上游管线用）。
        episode 文本默认取事实 content 拼接，保证引用链有真原文。"""
        from .extract import DictExtractor
        text = episode_text or "；".join(f["content"] for f in facts)
        ep = self.episodes.add(text, ts=ts, origin=origin)
        cands = DictExtractor(facts).extract("", ts=ts)
        return self._ingest(cands, ep.id, origin, default_ts=ts)

    def _ingest(self, candidates: list[FactCandidate], episode_id: str,
                origin: str, default_ts: Optional[str]) -> list[MemoryItem]:
        ingested: list[MemoryItem] = []
        for cand in candidates:
            item = cand.to_item(source=SourceRef(
                episode_id=episode_id, ts=cand.valid_from or default_ts,
                origin=origin))
            validate_source(item)                       # 不变量 1
            # 「复活需复核」：新候选重提一个已被 supersede 的旧值 → 可疑的迟到复述，
            # 不允许靠时间戳直接翻盘（stale recall vs 更正，需 LLM/人工裁决）
            resurrected = [
                old for old in self.store.history_of(item.key)
                if old.status == Status.SUPERSEDED
                and canonical_text(old.object) == canonical_text(item.object)
                and (old.valid_to is None or (item.valid_from or "") >= old.valid_to)
            ]
            if resurrected:
                item.status = Status.PENDING
                self.store.put(item)
                self._logs.append(ResolutionLog(
                    item_id=item.id, new_item_id=None, op="DEFER_LLM",
                    evidence=(f"resurrection: '{item.object}' was superseded by "
                              f"item {resurrected[0].id}; "
                              "stale recall vs correction needs review"),
                    judge="manual"))
                continue
            conflicts = self.store.current_by_key(item.key)
            # 未声明为函数型的 predicate：多值关系，同键不同 object 不做 SUPERSEDE
            if (self.functional is not None
                    and canonical_text(item.predicate) not in self.functional
                    and not item.qualifier):
                for res in self.resolver.resolve_multivalue(item, conflicts):
                    self._apply(res, item)
                if item.status in (Status.CURRENT, Status.PENDING):
                    ingested.append(item)
                continue
            for res in self.resolver.resolve(item, conflicts):
                self._apply(res, item)
            if item.status in (Status.CURRENT, Status.PENDING):
                ingested.append(item)
        return ingested

    def _apply(self, res: Resolution, item: MemoryItem) -> None:
        if res.op == "ADD":
            self.store.put(item)
            self.graph.add_triple(item.subject, item.predicate, item.object)
        elif res.op == "SUPERSEDE":
            self.store.supersede(res.matched_item, item)
            self.graph.add_triple(item.subject, item.predicate, item.object)
        elif res.op == "NOOP":
            # 重复候选不入库，但也不浪费：【抄 graphiti duplicate→episodes 追加】
            # 把新 episode 挂到已有条目做多源印证（confidence 微涨，封顶 1.0）
            old = res.matched_item
            if old is not None:
                old.corroborated_by.append(item.source.episode_id)
                old.confidence = min(1.0, old.confidence + 0.05)
                self.store.put(old)
            item.status = Status.DELETED
        elif res.op == "KEEP_BOTH":
            self.store.put(item)
            self.graph.add_triple(item.subject, item.predicate, item.object)
        # DEFER_LLM：item 已置 PENDING，入 pending 队列（持久可见，待 LLM/人工复核）
        if res.op == "DEFER_LLM":
            self.store.put(item)
        if res.op in ("ADD", "SUPERSEDE", "KEEP_BOTH", "NOOP", "DEFER_LLM"):
            self._logs.append(ResolutionLog(
                item_id=(res.matched_item.id if res.matched_item else
                         (res.new_item.id if res.new_item else "-")),
                new_item_id=item.id if res.op in ("ADD", "SUPERSEDE") else None,
                op=res.op, evidence=res.evidence, judge=res.judge))

    # ── 查询 ───────────────────────────────────────────────
    def query(self, q: str, k: int = 5, include_history: bool = False) -> Answer:
        return self.retriever.query(q, k=k, include_history=include_history)

    def fact_history(self, subject: str, predicate: str) -> list[MemoryItem]:
        """一条事实的完整演化链（superseded 永续）。"""
        return self.store.history_of((canonical_text(subject),
                                      canonical_text(predicate)))

    # ── ⑥ 动态更新：时间衰减 ──────────────────────────────
    def decay(self, half_life_days: float = 30.0,
              floor: float = 0.3) -> int:
        """procedural/episodic 记忆按 age 衰减 confidence（semantic 不衰减）。"""
        now = time.time()
        n = 0
        for it in self.store.current_items():
            if it.memory_type.value == "semantic":
                continue
            age = now - _parse_iso(it.transacted_at)
            factor = 0.5 ** (age / 86400.0 / half_life_days)
            it.confidence = max(floor, it.confidence * factor)
            self.store.put(it)
            n += 1
        return n

    # ── 审计导出（差异化：裁决证据链可导出） ───────────────
    def export_audit(self) -> dict:
        return {
            "items": [i.to_dict() for i in self.store.items_including_history()],
            "resolutions": [l.to_dict() for l in self._logs],
        }

    # ── RFT 数据导出：更正对（初判 → 更正） ────────────────
    def export_correction_pairs(self) -> list[dict]:
        """每次 SUPERSEDE 天然构成一个『错误认知 → 正确保留』训练对。
        直接对接 AuditScope RFT 数据管线：旧事实=模型当时的错误输出，
        新事实=人工/系统更正后的目标输出，evidence=可写进 reward reason 的依据。
        """
        pairs = []
        for log in self._logs:
            if log.op != "SUPERSEDE" or not log.new_item_id:
                continue
            old = self.store.get(log.item_id)
            new = self.store.get(log.new_item_id)
            if old and new:
                pairs.append({
                    "wrong": old.to_dict(),      # 被更正的旧事实（永续保留）
                    "correct": new.to_dict(),    # 更正后的目标事实
                    "evidence": log.evidence,
                    "judge": log.judge,
                    "ts": log.ts,
                })
        return pairs


def _parse_iso(s: str) -> float:
    import datetime
    return datetime.datetime.fromisoformat(s.replace("Z", "+00:00")).timestamp()
