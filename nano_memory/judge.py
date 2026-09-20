"""④ 裁决的 LLM 兜底【对照 mem0 阶段 B；fork: mem0/configs/prompts.py DEFAULT_UPDATE_MEMORY_PROMPT】
make_llm_judge(): 返回 Resolver 需要的 judge 回调 (new, old) -> (op, evidence) | None。
transport 可注入（测试用 mock），默认 urllib 调 OpenAI 兼容端点（dsh/DeepSeek 均可）。

端点配置（本机 dsh）：
  export MEMENGINE_LLM_BASE_URL=http://127.0.0.1:3080/v1
  export MEMENGINE_LLM_API_KEY=<dsh key>
  export MEMENGINE_LLM_MODEL=<dsh 模型名>
"""
from __future__ import annotations

import json
import os
import urllib.request
from typing import Callable, Optional

from .schema import MemoryItem

JUDGE_PROMPT = """你是一个记忆系统的冲突仲裁器。判断新事实与旧事实的关系：
- "SUPERSEDE"：新事实更新了旧事实描述的同一状态（如换了城市/换了设备/根因更正）
- "NOOP"：新事实与旧事实表达的是同一件事
- "KEEP_BOTH"：两者并不矛盾，可以并存
只输出 JSON：{{"op": "...", "evidence": "一句话依据"}}

旧事实：{old}
新事实：{new}"""

Transport = Callable[[str, dict], str]   # (url, payload) -> response text


def _urllib_transport(url: str, payload: dict) -> str:
    req = urllib.request.Request(
        url, data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json",
                 "Authorization": f"Bearer {os.environ.get('MEMENGINE_LLM_API_KEY', '')}"})
    with urllib.request.urlopen(req, timeout=60) as resp:
        return resp.read().decode()


def make_llm_judge(base_url: Optional[str] = None, model: Optional[str] = None,
                   transport: Optional[Transport] = None):
    base = base_url or os.environ.get("MEMENGINE_LLM_BASE_URL",
                                      "http://127.0.0.1:3080/v1")
    mdl = model or os.environ.get("MEMENGINE_LLM_MODEL", "deepseek-chat")
    send = transport or _urllib_transport
    endpoint = f"{base.rstrip('/')}/chat/completions"

    def judge(new: MemoryItem, old: MemoryItem) -> Optional[tuple[str, str]]:
        payload = {
            "model": mdl, "temperature": 0.0,
            "messages": [{"role": "user", "content": JUDGE_PROMPT.format(
                old=f"{old.content}（{old.subject} {old.predicate} {old.object}，"
                    f"valid {old.valid_from}）",
                new=f"{new.content}（{new.subject} {new.predicate} {new.object}，"
                    f"valid {new.valid_from}）")}],
            "response_format": {"type": "json_object"},
        }
        try:
            raw = send(endpoint, payload)
            data = json.loads(raw)
            content = data["choices"][0]["message"]["content"]
            verdict = json.loads(content)
            op = verdict.get("op")
            ev = verdict.get("evidence", "")
            if op in ("SUPERSEDE", "NOOP", "KEEP_BOTH", "ADD") and ev:
                return (op if op != "ADD" else "KEEP_BOTH", ev)
            return None
        except Exception as e:                       # 网络坏/格式坏 → 规则层兜底
            return (None, f"llm judge failed: {e}") and None
    return judge
