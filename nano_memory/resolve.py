"""④ 冲突消解【抄 graphiti edge_operations.py 双时态失效；LLM 仲裁降级为兜底】
四档规则裁决（MemEngine 设计方案 §4）：
  NOOP       归一化后与已有事实完全一致 → 丢弃
  SUPERSEDE  同 (subject,predicate) 且时间可判 → 旧条 valid_to=新 valid_from
  KEEP_BOTH  键不同/object 正交 → 直接 ADD
  DEFER_LLM  时间缺失或语义纠缠 → judge 回调；无 judge → PENDING
每次裁决必产生 evidence 非空的 ResolutionLog（不变量 4）。
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Optional

from .schema import (
    FactCandidate,
    MemoryItem,
    ResolutionLog,
    Status,
    canonical_text,
)

LLMJudge = Callable[[FactCandidate, MemoryItem], "Optional[tuple[str, str]]"]
# judge 返回 (op, evidence)，op ∈ {ADD, NOOP, SUPERSEDE, KEEP_BOTH}；None = 无法裁决


@dataclass
class Resolution:
    op: str
    evidence: str
    judge: str
    matched_item: Optional[MemoryItem] = None  # 匹配到的已有条目（SUPERSEDE=被顶掉者，NOOP=被印证者）
    new_item: Optional[MemoryItem] = None      # ADD/SUPERSEDE 时生成的新条目


class Resolver:
    def __init__(self, judge: Optional[LLMJudge] = None) -> None:
        self._judge = judge

    def resolve(self, new: MemoryItem, conflicts: list[MemoryItem]) -> list[Resolution]:
        """对一条新事实与其全部冲突项做裁决，返回按序应用的 Resolution 列表。"""
        out: list[Resolution] = []
        for old in conflicts:
            out.append(self._resolve_one(new, old))
        if not conflicts:
            out.append(self._add(new, evidence="no conflicting (subject,predicate)"))
        return out

    def resolve_multivalue(self, new: MemoryItem, conflicts: list[MemoryItem]) -> list[Resolution]:
        """多值关系（未声明函数型）：不同 object 正交并存，仅内容完全一致时 NOOP。"""
        out = []
        for old in conflicts:
            if canonical_text(new.content) == canonical_text(old.content):
                out.append(Resolution("NOOP", f"identical content of item {old.id}",
                                      "rules", matched_item=old))
                continue
            out.append(self._add(new, evidence=(
                f"multi-valued predicate {new.key[1]}: coexists with item {old.id}")))
            break  # 一条并存证据即可，其余冲突项同理
        if not conflicts:
            out.append(self._add(new, evidence="no conflicts"))
        return out

    # ── 单对裁决（调用方保证 same key：current_by_key 只回同键） ──
    def _resolve_one(self, new: MemoryItem, old: MemoryItem) -> Resolution:
        # 1) NOOP：完全重复
        if canonical_text(new.content) == canonical_text(old.content):
            return Resolution("NOOP", f"identical content of item {old.id}", "rules",
                              matched_item=old)  # 重复=印证机会，交 lifecycle 累积

        # 2) 同键、object 变化：必须二选一，不能 KEEP_BOTH（不变量 3）
        #    例外：限定条件不同（"工作日"vs"周末"）→ 正交并存
        if (new.qualifier and old.qualifier
                and canonical_text(new.qualifier) != canonical_text(old.qualifier)):
            return self._add(new, evidence=(
                f"different qualifiers coexist: '{new.qualifier}' vs '{old.qualifier}' "
                f"(item {old.id})"))
        if canonical_text(new.object) != canonical_text(old.object):
            if new.valid_from and old.valid_from:
                if new.valid_from >= old.valid_from:
                    return Resolution(
                        "SUPERSEDE",
                        f"same key {new.key}, object '{old.object}' -> '{new.object}', "
                        f"t {old.valid_from} <= {new.valid_from}",
                        "rules", matched_item=old, new_item=new,
                    )
                # 新候选声称的是更早的状态（迟到信息）→ 交 LLM/人工
                return self._defer(new, old,
                                   f"stale candidate: {new.valid_from} < {old.valid_from}")
            return self._defer(new, old, "same key but missing temporal axis")

        # 3) 同键同 object 但内容措辞不同（改写/近似重复）→ LLM 兜底
        return self._defer(new, old, "same (subject,predicate,object), wording differs")

    def _add(self, new: MemoryItem, evidence: str) -> Resolution:
        return Resolution("ADD", evidence, "rules", new_item=new)

    def _defer(self, new: MemoryItem, old: MemoryItem, reason: str) -> Resolution:
        if self._judge is not None:
            verdict = self._judge(new, old)
            if verdict is not None:
                op, ev = verdict
                target = old if op == "SUPERSEDE" else None
                item = new if op in ("ADD", "SUPERSEDE") else None
                return Resolution(op, f"llm judge: {ev}", "llm",
                                  matched_item=target, new_item=item)
            reason += "; llm judge returned no verdict"
        new.status = Status.PENDING
        return Resolution("DEFER_LLM",
                          f"item {new.id} pending vs {old.id}: {reason}",
                          "manual")
