"""⑤ 来源追溯【抄 graphiti EpisodicNode：provenance by construction】
EpisodeStore：原文 append-only；无来源的事实不允许入库（校验器在此）。
"""
from __future__ import annotations

from typing import Optional

from .schema import Episode, MemoryItem, SourceRef, _now_iso


class EpisodeStore:
    """原文块仓库：Episode 是一切事实的溯源终点。"""

    def __init__(self) -> None:
        self._episodes: dict[str, Episode] = {}

    def add(self, text: str, ts: Optional[str] = None, origin: str = "conversation") -> Episode:
        ep = Episode(text=text, ts=ts or _now_iso(), origin=origin)
        self._episodes[ep.id] = ep
        return ep

    def get(self, episode_id: str) -> Optional[Episode]:
        return self._episodes.get(episode_id)

    def source_text(self, ref: SourceRef, width: int = 80) -> str:
        """引用链终点：从 SourceRef 还原原文片段。"""
        ep = self._episodes.get(ref.episode_id)
        if ep is None:
            return "<missing episode>"
        return ep.text[:width]

    def all(self) -> list[Episode]:
        return list(self._episodes.values())


def validate_source(item: MemoryItem) -> None:
    """不变量 1：source 缺失/指向未知 episode 的 item 拒绝入库。"""
    if item.source is None or not item.source.episode_id:
        raise ValueError(f"item {item.id} rejected: no provenance (invariant #1)")
