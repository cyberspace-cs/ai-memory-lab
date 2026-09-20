"""检索（六维的出口）【mem0 hybrid + graphiti 图距离 rerank】
BM25（中文 bigram + 英文词）+ 实体图扩展（multi_hop 命中加权）→ current-only → 引用链。
"""
from __future__ import annotations

import math
import re
from dataclasses import dataclass, field
from typing import Optional

from .graph import EntityGraph
from .schema import MemoryItem, SourceRef, canonical_text
from .store import MemoryStore
from .vectors import VectorIndex

_TOKEN_RE = re.compile(r"[a-zA-Z0-9_]+")

# 中文问句 → predicate 词典加权（离线检索的语义桥梁；v2 换向量召回后仍作 rerank 特征）
_PRED_LEXICON: dict[str, list[str]] = {
    "lives_in": ["住在", "搬到", "住在哪里", "哪个城市", "居住"],
    "works_at": ["工作", "入职", "雇主", "哪家公司"],
    "uses_phone": ["手机", "用什么设备"],
    "likes": ["喜欢", "爱喝", "偏好"],
    "root_cause": ["根因", "什么原因", "原因是什么", "定位到"],
}


def _predicates_in_query(query: str) -> set[str]:
    hits = set()
    for pred, kws in _PRED_LEXICON.items():
        if any(kw in query for kw in kws):
            hits.add(pred)
    return hits


def _terms(text: str) -> list[str]:
    """中英混合分词：英文按词，中文按 bigram（单字也保留，覆盖单字实体）。"""
    zh = re.sub(r"[^\u4e00-\u9fff]", " ", text)
    toks = _TOKEN_RE.findall(text)
    for part in zh.split():
        if len(part) == 1:
            toks.append(part)
        else:
            toks += [part[i:i + 2] for i in range(len(part) - 1)] or [part]
    return [t for t in toks if t]


@dataclass
class Citation:
    fact_id: str
    episode_id: str
    quote: str          # 原文片段
    origin: str

    def to_dict(self) -> dict:
        return dict(fact_id=self.fact_id, episode_id=self.episode_id,
                    quote=self.quote, origin=self.origin)


@dataclass
class Answer:
    query: str
    items: list[MemoryItem] = field(default_factory=list)
    citations: list[Citation] = field(default_factory=list)

    def to_dict(self) -> dict:
        return dict(query=self.query,
                    facts=[i.to_dict() for i in self.items],
                    citations=[c.to_dict() for c in self.citations])


class Retriever:
    def __init__(self, store: MemoryStore, graph: EntityGraph,
                 source_text_fn, hops: int = 1) -> None:
        self._store = store
        self._graph = graph
        self._source_text = source_text_fn     # EpisodeStore.source_text
        self._hops = hops

    # ── BM25（轻量实现） ──────────────────────────────────
    def _bm25_scores(self, query: str, items: list[MemoryItem]) -> dict[str, float]:
        k1, b = 1.5, 0.75
        docs = {i.id: _terms(f"{i.content} {' '.join(i.entities)}") for i in items}
        avgdl = sum(map(len, docs.values())) / max(len(docs), 1)
        df: dict[str, int] = {}
        for toks in docs.values():
            for t in set(toks):
                df[t] = df.get(t, 0) + 1
        q_terms = _terms(query)
        scores: dict[str, float] = {}
        for i in items:
            toks, dl = docs[i.id], max(len(docs[i.id]), 1)
            s = 0.0
            for t in q_terms:
                if t not in toks:
                    continue
                idf = math.log(1 + (len(items) - df.get(t, 0) + 0.5) / (df.get(t, 0) + 0.5))
                tf = toks.count(t)
                s += idf * tf * (k1 + 1) / (tf + k1 * (1 - b + b * dl / avgdl))
            if s:
                scores[i.id] = s
        return scores

    def query(self, query: str, k: int = 5,
              include_history: bool = False) -> Answer:
        pool = (self._store.items_including_history() if include_history
                else self._store.current_items())
        if not pool:
            return Answer(query=query)

        # ── 三路召回 + RRF 融合【mem0 hybrid + graphiti rerank 的融合版】 ──
        RRF_K = 60
        fused: dict[str, float] = {}

        # 路 1：BM25
        bm25 = self._bm25_scores(query, pool)
        for rank, (iid, _) in enumerate(
                sorted(bm25.items(), key=lambda x: x[1], reverse=True)):
            fused[iid] = fused.get(iid, 0) + 1.0 / (RRF_K + rank + 1)

        # 路 2：TF-IDF 向量（sqlite-vec 版 v2 替换）
        vec_index = VectorIndex().fit(
            {i.id: f"{i.content} {' '.join(i.entities)}" for i in pool})
        for rank, (iid, _) in enumerate(vec_index.search(query, k=len(pool))):
            fused[iid] = fused.get(iid, 0) + 1.0 / (RRF_K + rank + 1)

        scores = bm25  # 保留原始分数供调试

        # 图扩展 + 谓词词典加权
        q_canon = canonical_text(query)
        q_preds = _predicates_in_query(query)
        boost: dict[str, float] = {}
        for item in pool:
            if item.predicate in q_preds:
                boost[item.id] = boost.get(item.id, 0) + 2.0
        for item in pool:
            for ent in item.entities:
                c = self._graph.canonical(ent)
                if c and c in q_canon:
                    boost[item.id] = boost.get(item.id, 0) + 0.5
                for nb in self._graph.multi_hop(ent, self._hops):
                    if nb and nb in q_canon:
                        boost[item.id] = boost.get(item.id, 0) + 0.2

        ranked = sorted(
            pool,
            key=lambda i: fused.get(i.id, 0.0) + boost.get(i.id, 0.0),
            reverse=True,
        )[:k]
        ans = Answer(query=query, items=ranked)
        for it in ranked:
            ep = it.source
            ans.citations.append(Citation(
                fact_id=it.id, episode_id=ep.episode_id,
                quote=self._source_text(ep), origin=ep.origin))
        return ans
