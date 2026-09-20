"""验收语料 B：AuditScope 事件单（告警 → 初判 → 更正 → 复盘）。
验收点：根因更正走 SUPERSEDE；初判永续可查（RFT 更正对）；引用链可解析。
"""
import unittest

from nano_memory.extract import DictExtractor
from nano_memory.lifecycle import MemEngine
from nano_memory.schema import Status


INCIDENT_FACTS = [
    # —— 09:00 P0 告警 ——
    dict(memory_type="episodic", content="prod-orderservice p99 延迟 3.2s，触发告警 latency-high",
         subject="prod-orderservice", predicate="has_alert", object="p99 延迟 3.2s",
         valid_from="2026-09-20T09:00:00"),
    # —— 09:15 值班初判 ——
    dict(memory_type="semantic", content="初判：下游 pay-service 超时引发 orderservice 延迟雪崩",
         subject="orderservice-p99-事件", predicate="root_cause", object="pay-service 超时",
         valid_from="2026-09-20T09:15:00"),
    # —— 09:40 定位更正 ——
    dict(memory_type="semantic", content="更正：根因是 orderservice 连接池耗尽（maxPool=50 打满），pay-service 超时是结果不是原因",
         subject="orderservice-p99-事件", predicate="root_cause", object="连接池耗尽",
         valid_from="2026-09-20T09:40:00"),
    # —— 正交事实：告警恢复动作 ——
    dict(memory_type="procedural", content="处置动作：连接池扩容至 200 并预热",
         subject="prod-orderservice", predicate="mitigated_by", object="连接池扩容 200",
         valid_from="2026-09-20T09:45:00"),
]


def build_engine() -> MemEngine:
    eng = MemEngine(extractor=DictExtractor([]))
    # 逐条作为独立 episode 注入（贴近事件单时间线）
    for f in INCIDENT_FACTS:
        eng.add_facts([f], ts=f["valid_from"], origin="incident")
    return eng


class TestIncidentCorpus(unittest.TestCase):
    def setUp(self):
        self.eng = build_engine()

    def test_rootcause_supersede(self):
        cur = self.eng.store.current_by_key(("orderservice-p99-事件", "root_cause"))
        self.assertEqual(len(cur), 1)
        self.assertIn("连接池耗尽", cur[0].object)

    def test_correction_pair_for_rft(self):
        pairs = self.eng.export_correction_pairs()
        self.assertEqual(len(pairs), 1)
        self.assertIn("pay-service", pairs[0]["wrong"]["object"])
        self.assertIn("连接池耗尽", pairs[0]["correct"]["object"])
        self.assertTrue(pairs[0]["evidence"])          # reward reason 有据可写

    def test_query_current_and_history(self):
        cur = self.eng.query("orderservice 事件的根因是什么")
        self.assertEqual(cur.items[0].predicate, "root_cause")     # 词典加权后根因第一
        self.assertIn("连接池耗尽", cur.items[0].object)
        hist = self.eng.query("orderservice 事件的根因是什么", include_history=True)
        objs = {i.object for i in hist.items}
        self.assertIn("pay-service 超时", objs)          # 初判永续（'当时的真相'）
        self.assertIn("连接池耗尽", objs)

    def test_orthogonal_fact_kept(self):
        cur = self.eng.store.current_by_key(("prod-orderservice", "mitigated_by"))
        self.assertEqual(len(cur), 1)
        self.assertIn("扩容", cur[0].object)

    def test_provenance_chain(self):
        ans = self.eng.query("orderservice 事件的根因是什么")
        c = ans.citations[0]
        self.assertEqual(c.origin, "incident")
        self.assertTrue(c.episode_id)
        self.assertIn("更正", c.quote)

    def test_invariants_hold(self):
        audit = self.eng.export_audit()
        keys = {}
        for it in audit["items"]:
            if it["status"] == "current":
                k = (it["subject"].lower(), it["predicate"].lower())
                keys[k] = keys.get(k, 0) + 1
        self.assertTrue(all(v <= 1 for v in keys.values()))   # 不变量 3
        for log in audit["resolutions"]:
            self.assertTrue(log["evidence"].strip())          # 不变量 4


if __name__ == "__main__":
    unittest.main()
