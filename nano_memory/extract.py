"""① 信息提取【抄 mem0 两阶段；对照 fork: mem0/memory/main.py add() -> _add_to_vector_store】
三级提取器：LLMExtractor（可选，OpenAI 兼容端点）→ RuleExtractor（离线）
→ DictExtractor（测试/结构化输入）。产物统一 FactCandidate。

mem0 两阶段在这里的映射：
  阶段 A（事实抽取 prompt）      -> BaseExtractor.extract() 单次调用
  阶段 B（与旧记忆对比后裁决）   -> Resolver（我们把裁决从 LLM 降级为规则优先）
"""
from __future__ import annotations

import json
import re
from abc import ABC, abstractmethod
from typing import Any, Optional

from .schema import FactCandidate, MemoryType

# ── 阶段 A prompt（mem0 事实抽取 prompt 的精简对照版，中英双语） ──
EXTRACTION_PROMPT = """你是一个记忆抽取器。从下面的输入中抽取关于主体的、可长期成立的事实。
每条事实输出 JSON：{"subject","predicate","object","memory_type","valid_from"}。
- memory_type ∈ semantic|episodic|procedural
- valid_from 是该事实开始成立的时间（ISO 日期），没有则 null
- 只抽"状态型"事实（住址/雇主/设备/偏好），不抽闲聊
输入：
{input}
输出 JSON 数组："""

# 阶段 B prompt 保留在 mem0 fork：mem0/configs/prompts.py 的 DEFAULT_UPDATE_MEMORY_PROMPT


class BaseExtractor(ABC):
    @abstractmethod
    def extract(self, text: str, ts: Optional[str] = None) -> list[FactCandidate]:
        ...


class DictExtractor(BaseExtractor):
    """结构化输入直通（测试与上游管线对接用）。"""

    def __init__(self, facts: list[dict[str, Any]]) -> None:
        self._facts = facts

    def extract(self, text: str, ts: Optional[str] = None) -> list[FactCandidate]:
        return [
            FactCandidate(
                memory_type=MemoryType(f.get("memory_type", "semantic")),
                content=f["content"],
                subject=f["subject"], predicate=f["predicate"], object=f["object"],
                # 注意：不回填 ts。ts 是事务时间，valid_from 是业务时间，
                # 二者混用会掩盖"业务时间未知"→ 冲突裁决应走 DEFER
                valid_from=f.get("valid_from"),
                qualifier=f.get("qualifier", ""),
                confidence=f.get("confidence", 0.9),
            )
            for f in self._facts
        ]


class RuleExtractor(BaseExtractor):
    """离线规则提取：覆盖常见中英模式，保证无 LLM 也能全链路跑通。"""

    # (regex, predicate, memory_type)；组：1=subject 2=object
    _ZH_PATTERNS = [
        (r"([\w\u4e00-\u9fff·]+)住在([\w\u4e00-\u9fff]+)", "lives_in", MemoryType.SEMANTIC),
        (r"([\w\u4e00-\u9fff·]+)搬到了([\w\u4e00-\u9fff]+)", "lives_in", MemoryType.SEMANTIC),
        (r"([\w\u4e00-\u9fff·]+)在([\w\u4e00-\u9fff·]+)工作", "works_at", MemoryType.SEMANTIC),
        (r"([\w\u4e00-\u9fff·]+)入职了([\w\u4e00-\u9fff·]+)", "works_at", MemoryType.SEMANTIC),
        (r"([\w\u4e00-\u9fff·]+)换(?:了)?(?:新)?手机.*?(?:是|用)\s*([\w\s\-+]+)", "uses_phone", MemoryType.SEMANTIC),
        (r"([\w\u4e00-\u9fff·]+)现在用([\w\s\-+]+)手机", "uses_phone", MemoryType.SEMANTIC),
        (r"([\w\u4e00-\u9fff·]+)喜欢([\w\u4e00-\u9fff·]+)", "likes", MemoryType.PROCEDURAL),
        (r"([\w\u4e00-\u9fff·]+)不再喜欢([\w\u4e00-\u9fff·]+)", "dislikes", MemoryType.PROCEDURAL),
    ]
    _EN_PATTERNS = [
        (r"(\w[\w\s]*?)\s+(?:works at|joined)\s+([\w\s\.]+)", "works_at", MemoryType.SEMANTIC),
        (r"(\w[\w\s]*?)\s+(?:lives in|moved to)\s+([\w\s]+)", "lives_in", MemoryType.SEMANTIC),
        (r"(\w[\w\s]*?)\s+likes\s+([\w\s]+)", "likes", MemoryType.PROCEDURAL),
    ]

    def extract(self, text: str, ts: Optional[str] = None) -> list[FactCandidate]:
        out: list[FactCandidate] = []
        for sent in re.split(r"[。.!?\n；;]", text):
            sent = sent.strip()
            if not sent:
                continue
            for pat, pred, mtype in self._ZH_PATTERNS + self._EN_PATTERNS:
                m = re.search(pat, sent)
                if m:
                    subj, obj = m.group(1).strip(), m.group(2).strip()
                    out.append(FactCandidate(
                        memory_type=mtype, content=sent,
                        subject=subj, predicate=pred, object=obj,
                        valid_from=ts, confidence=0.6,
                    ))
                    break  # 每句取第一个命中，避免重复
        return out


class LLMExtractor(BaseExtractor):
    """OpenAI 兼容端点的两阶段阶段 A（dsh/DeepSeek 均可）。v0 留桩不阻塞离线测试。"""

    def __init__(self, base_url: str, api_key: str, model: str = "deepseek-chat",
                 temperature: float = 0.0) -> None:
        self.base_url, self.api_key, self.model = base_url, api_key, model
        self.temperature = temperature

    def extract(self, text: str, ts: Optional[str] = None) -> list[FactCandidate]:
        import urllib.request  # 延迟导入，离线环境不受影响
        body = json.dumps({
            "model": self.model, "temperature": self.temperature,
            "messages": [{"role": "user", "content": EXTRACTION_PROMPT.format(input=text)}],
            "response_format": {"type": "json_object"},
        }).encode()
        req = urllib.request.Request(
            f"{self.base_url.rstrip('/')}/chat/completions", data=body,
            headers={"Authorization": f"Bearer {self.api_key}",
                     "Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=60) as resp:
            data = json.load(resp)
        raw = json.loads(data["choices"][0]["message"]["content"])
        items = raw.get("facts", raw) if isinstance(raw, dict) else raw
        out = []
        for f in items:
            out.append(FactCandidate(
                memory_type=MemoryType(f.get("memory_type", "semantic")),
                content=f.get("content", f"{f['subject']} {f['predicate']} {f['object']}"),
                subject=f["subject"], predicate=f["predicate"], object=f["object"],
                valid_from=f.get("valid_from") or ts, confidence=0.9,
            ))
        return out
