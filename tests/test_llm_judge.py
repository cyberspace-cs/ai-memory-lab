"""LLM 裁决路径：mock transport 打通 judge → Resolver → engine（不依赖网络）。"""
import json
import unittest

from nano_memory.extract import DictExtractor
from nano_memory.judge import JUDGE_PROMPT, make_llm_judge
from nano_memory.lifecycle import MemEngine
from nano_memory.schema import Status


def fake_transport(payload_by_call):
    """按调用序返回预设的 chat.completions 响应。"""
    calls = {"n": 0}

    def _send(url, payload):
        i = calls["n"]
        calls["n"] += 1
        content = payload_by_call[i]
        return json.dumps({"choices": [{"message": {"content": json.dumps(content)}}]})

    _send.calls = calls
    return _send


class TestLLMJudge(unittest.TestCase):
    def _engine(self, verdict: dict):
        send = fake_transport([verdict])
        judge = make_llm_judge(base_url="http://mock/v1", model="mock",
                               transport=send)
        eng = MemEngine(extractor=DictExtractor([]), judge=judge)
        return eng, send

    def test_llm_supersede_when_time_missing(self):
        eng, send = self._engine({"op": "SUPERSEDE", "evidence": "新偏好替代旧偏好"})
        eng.add_facts([dict(memory_type="procedural", content="用户喜欢喝冰美式",
                            subject="用户", predicate="likes_drink", object="冰美式",
                            valid_from="2026-02-01")], ts="2026-02-01")
        eng.add_facts([dict(memory_type="procedural", content="用户现在喜欢喝拿铁",
                            subject="用户", predicate="likes_drink", object="拿铁",
                            valid_from=None)], ts="2026-08-01")
        cur = eng.store.current_by_key(("用户", "likes_drink"))
        self.assertEqual(len(cur), 1)
        self.assertEqual(cur[0].object, "拿铁")        # LLM 裁决生效
        logs = eng.export_audit()["resolutions"]
        self.assertTrue(any(l["op"] == "SUPERSEDE" and l["judge"] == "llm"
                            for l in logs))

    def test_llm_noop(self):
        eng, send = self._engine({"op": "NOOP", "evidence": "同一事实换措辞"})
        eng.add_facts([dict(memory_type="semantic", content="用户住在上海",
                            subject="用户", predicate="lives_in", object="上海",
                            valid_from="2026-01-10")], ts="2026-01-10")
        eng.add_facts([dict(memory_type="semantic", content="用户的居住地是上海",
                            subject="用户", predicate="lives_in", object="上海",
                            valid_from="2026-01-10")], ts="2026-01-11")
        cur = eng.store.current_by_key(("用户", "lives_in"))
        self.assertEqual(len(cur), 1)                  # 措辞版被 NOOP 掉
        self.assertEqual(cur[0].content, "用户住在上海")

    def test_judge_failure_falls_back(self):
        def bad_send(url, payload):
            raise IOError("dsh offline")
        judge = make_llm_judge(transport=bad_send)
        # 直接调用：故障时返回 None（回落规则层），且不抛异常
        from nano_memory.schema import FactCandidate, MemoryType, SourceRef
        cand = FactCandidate(memory_type=MemoryType.PROCEDURAL, content="x",
                             subject="用户", predicate="likes_drink", object="拿铁")
        a = cand.to_item(SourceRef(episode_id="e1"))
        b = cand.to_item(SourceRef(episode_id="e2"))
        self.assertIsNone(judge(b, a))
        eng = MemEngine(extractor=DictExtractor([]), judge=judge)
        eng.add_facts([dict(memory_type="procedural", content="用户喜欢喝冰美式",
                            subject="用户", predicate="likes_drink", object="冰美式",
                            valid_from="2026-02-01")], ts="2026-02-01")
        eng.add_facts([dict(memory_type="procedural", content="用户现在喜欢喝拿铁",
                            subject="用户", predicate="likes_drink", object="拿铁",
                            valid_from=None)], ts="2026-08-01")
        pend = [i for i in eng.store.items_including_history()
                if i.status == Status.PENDING]
        self.assertEqual(len(pend), 1)                 # LLM 挂了 → 回落 PENDING

    def test_prompt_shape(self):
        text = JUDGE_PROMPT.format(old="a", new="b")
        self.assertIn("SUPERSEDE", text)
        self.assertIn("NOOP", text)
        self.assertIn("KEEP_BOTH", text)


if __name__ == "__main__":
    unittest.main()
