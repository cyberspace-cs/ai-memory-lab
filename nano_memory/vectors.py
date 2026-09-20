"""向量召回第三路【纯 stdlib TF-IDF + cosine；sqlite-vec 适配位留 v2 侧车】
VectorIndex：fit(current pool) -> search(query, k)；与 BM25/图扩展并列为三路召回之一。
"""
from __future__ import annotations

import math
import re
from dataclasses import dataclass, field
from typing import Optional

_TOKEN_RE = re.compile(r"[a-zA-Z0-9_]+")


def _terms(text: str) -> list[str]:
    """中英混合分词（与 retrieve.py 同款：英文按词、中文 bigram+单字）。"""
    zh = re.sub(r"[^\u4e00-\u9fff]", " ", text)
    toks = _TOKEN_RE.findall(text)
    for part in zh.split():
        if len(part) == 1:
            toks.append(part)
        else:
            toks += [part[i:i + 2] for i in range(len(part) - 1)] or [part]
    return [t for t in toks if t]


@dataclass
class VectorIndex:
    """TF-IDF 向量索引（内存版）。sqlite-vec 版 v2 再加，接口对齐 search()。"""
    _vocab: dict[str, int] = field(default_factory=dict)
    _idf: list[float] = field(default_factory=list)
    _matrix: dict[str, list[float]] = field(default_factory=dict)   # item_id -> 向量

    def fit(self, docs: dict[str, str]) -> "VectorIndex":
        """docs: item_id -> 文本。构建词表、IDF 与文档向量。"""
        tok_docs = {k: _terms(v) for k, v in docs.items()}
        df: dict[str, int] = {}
        for toks in tok_docs.values():
            for t in set(toks):
                df[t] = df.get(t, 0) + 1
        n = max(len(tok_docs), 1)
        self._vocab = {t: i for i, t in enumerate(sorted(df))}
        self._idf = [0.0] * len(self._vocab)
        for t, i in self._vocab.items():
            self._idf[i] = math.log((n + 1) / (df[t] + 1)) + 1.0
        self._matrix = {k: self._vectorize(toks) for k, toks in tok_docs.items()}
        return self

    def _vectorize(self, toks: list[str]) -> list[float]:
        vec = [0.0] * len(self._vocab)
        for t in toks:
            i = self._vocab.get(t)
            if i is not None:
                vec[i] += 1.0
        for i, v in enumerate(vec):
            if v:
                vec[i] = v * self._idf[i]
        norm = math.sqrt(sum(v * v for v in vec)) or 1.0
        return [v / norm for v in vec]

    def search(self, query: str, k: int = 10) -> list[tuple[str, float]]:
        """返回 [(item_id, cosine)] 按相似度降序。"""
        if not self._matrix:
            return []
        q = self._vectorize(_terms(query))
        scored = []
        for item_id, vec in self._matrix.items():
            sim = sum(a * b for a, b in zip(q, vec))
            if sim > 0:
                scored.append((item_id, sim))
        scored.sort(key=lambda x: x[1], reverse=True)
        return scored[:k]
