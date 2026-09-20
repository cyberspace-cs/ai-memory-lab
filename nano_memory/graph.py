"""③ 实体关系建模【抄 graphiti add_episode 实体抽取+去重】
EntityGraph：纯标准库邻接表 + 实体归一（canonical/别名表），图后端接口留适配位。
"""
from __future__ import annotations

from collections import defaultdict, deque
from typing import Optional

from .schema import canonical_text


class EntityGraph:
    def __init__(self, aliases: Optional[dict[str, str]] = None) -> None:
        self._adj: dict[str, set[str]] = defaultdict(set)   # 实体（canonical）邻接
        self._triples: set[tuple[str, str, str]] = set()    # (s, p, o) canonical
        self._alias: dict[str, str] = {                     # 别名 -> canonical
            canonical_text(k): canonical_text(v) for k, v in (aliases or {}).items()
        }

    # ── 写入 ───────────────────────────────────────────────
    def add_triple(self, subject: str, predicate: str, obj: str) -> tuple[str, str, str]:
        s, p, o = self.canonical(subject), canonical_text(predicate), self.canonical(obj)
        self._triples.add((s, p, o))
        self._adj[s].add(o)
        self._adj[o].add(s)
        return (s, p, o)

    def merge_entity(self, src: str, dst: str) -> None:
        """实体归一：src 并入 dst（graphiti 同义实体合并的最简版）。"""
        s, d = self.canonical(src), self.canonical(dst)
        if s == d:
            return
        self._alias[s] = d
        for (a, p, b) in list(self._triples):
            na, nb = self.canonical(a), self.canonical(b)
            if s in (na, nb):
                self._triples.discard((a, p, b))
                self._triples.add((
                    d if na == s else na, p, d if nb == s else nb))
        for k in list(self._adj):
            if k == s:
                self._adj[d] |= self._adj.pop(k)
            elif s in self._adj[k]:
                self._adj[k].discard(s)
                self._adj[k].add(d)

    # ── 查询 ───────────────────────────────────────────────
    def canonical(self, name: str) -> str:
        k = canonical_text(name)
        seen = set()
        while k in self._alias and k not in seen:   # 链式别名收敛
            seen.add(k)
            k = self._alias[k]
        return k

    def neighbors(self, name: str) -> set[str]:
        return set(self._adj.get(self.canonical(name), set()))

    def multi_hop(self, name: str, hops: int = 2) -> set[str]:
        """图游走召回：BFS 取 hops 邻域。"""
        start = self.canonical(name)
        seen, frontier = {start}, {start}
        for _ in range(hops):
            nxt = set()
            for node in frontier:
                nxt |= self._adj.get(node, set())
            frontier = nxt - seen
            seen |= frontier
        seen.discard(start)
        return seen

    def path(self, src: str, dst: str) -> Optional[list[str]]:
        """BFS 最短关系路径（可解释性输出）。"""
        s, d = self.canonical(src), self.canonical(dst)
        if s == d:
            return [s]
        prev: dict[str, str] = {s: ""}
        q = deque([s])
        while q:
            cur = q.popleft()
            for nb in self._adj.get(cur, ()):
                if nb in prev:
                    continue
                prev[nb] = cur
                if nb == d:
                    path, node = [], d
                    while node:
                        path.append(node)
                        node = prev[node]
                    return list(reversed(path))
                q.append(nb)
        return None

    def triples(self) -> list[tuple[str, str, str]]:
        return sorted(self._triples)
