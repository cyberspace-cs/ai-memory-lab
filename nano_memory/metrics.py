"""评测指标（设计方案 §6）：current 准确率 / supersede 正确率 / 引用可解析率 / 印证覆盖。
用期望表驱动：expect = {key: {"current": 期望 object, "history": 期望出现过的 object 集合}}
"""
from __future__ import annotations

from dataclasses import dataclass, field

from .lifecycle import MemEngine
from .schema import Status, canonical_text


@dataclass
class MetricReport:
    current_accuracy: float = 0.0
    supersede_correctness: float = 0.0
    citation_resolvability: float = 0.0
    corroboration_coverage: float = 0.0
    details: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {k: v for k, v in self.__dict__.items() if k != "details"} | {
            "details": self.details}


def evaluate(engine: MemEngine, expect_current: dict[tuple[str, str], list[str]],
             expect_history: dict[tuple[str, str], set[str]] | None = None) -> MetricReport:
    """expect_current: key -> 期望的 current object 列表（含 qualifier 场景的多条）。
    expect_history: key -> 期望在演化链中出现过的 object 集合（supersede 正确率）。"""
    rep = MetricReport()

    # 1) current 准确率
    ok = 0
    total = len(expect_current)
    for key, expected_objs in expect_current.items():
        got = sorted(canonical_text(i.object) for i in engine.store.current_by_key(key))
        want = sorted(canonical_text(o) for o in expected_objs)
        if got == want:
            ok += 1
        else:
            rep.details.append(f"current mismatch {key}: want={want} got={got}")
    rep.current_accuracy = ok / max(total, 1)

    # 2) supersede 正确率：历史链包含全部期望值，且旧条带 invalidated_at
    hist_ok, hist_total = 0, 0
    for key, expected_set in (expect_history or {}).items():
        hist_total += 1
        chain = engine.store.history_of(key)
        got = {canonical_text(i.object) for i in chain}
        if expected_set <= got and all(
                i.invalidated_at is not None for i in chain if i.status == Status.SUPERSEDED):
            hist_ok += 1
        else:
            rep.details.append(f"history mismatch {key}: want<={expected_set} got={got}")
    rep.supersede_correctness = hist_ok / max(hist_total, 1)

    # 3) 引用可解析率：current 条目的引用链必须还原出非缺失原文
    resolvable, cited = 0, 0
    for it in engine.store.current_items():
        cited += 1
        text = engine.episodes.source_text(it.source)
        if text and "<missing" not in text and text != "<structured facts>":
            resolvable += 1
    rep.citation_resolvability = resolvable / max(cited, 1)

    # 4) 印证覆盖：被 NOOP 印证过的条目占比（信号是否在积累）
    corroborated = sum(1 for i in engine.store.current_items() if i.corroborated_by)
    rep.corroboration_coverage = corroborated / max(len(engine.store.current_items()), 1)
    return rep
