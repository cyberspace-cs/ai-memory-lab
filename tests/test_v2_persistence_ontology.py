"""v2 验收：SQLite 持久化往返 + 运维本体驱动。"""
import os
import tempfile
import unittest

from nano_memory.extract import DictExtractor
from nano_memory.lifecycle import MemEngine
from nano_memory.ontology import ops_engine, ops_extraction_prompt, ops_functional_predicates
from nano_memory.persistence import load_engine, save_engine
from nano_memory.schema import Status


def _incident_engine(**kw):
    eng = MemEngine(extractor=DictExtractor([]), **kw)
    eng.add_facts([dict(memory_type="semantic",
                        content="初判：下游 pay-service 超时引发 orderservice 延迟雪崩",
                        subject="orderservice-p99-事件", predicate="root_cause",
                        object="pay-service 超时", valid_from="2026-09-20T09:15:00")],
                  ts="2026-09-20T09:15:00", origin="incident")
    eng.add_facts([dict(memory_type="semantic",
                        content="更正：根因是 orderservice 连接池耗尽",
                        subject="orderservice-p99-事件", predicate="root_cause",
                        object="连接池耗尽", valid_from="2026-09-20T09:40:00")],
                  ts="2026-09-20T09:40:00", origin="incident")
    eng.add_facts([dict(memory_type="episodic",
                        content="prod-orderservice p99 延迟 3.2s，触发告警",
                        subject="prod-orderservice", predicate="has_alert",
                        object="p99 延迟 3.2s", valid_from="2026-09-20T09:00:00")],
                  ts="2026-09-20T09:00:00", origin="incident")
    return eng


class TestPersistence(unittest.TestCase):
    def test_roundtrip(self):
        eng = _incident_engine()
        eng.add_episode("我住在上海，在昆仑万维工作。", ts="2026-01-10")  # RuleExtractor 路径
        path = os.path.join(tempfile.mkdtemp(), "mem.db")
        save_engine(eng, path)
        loaded = load_engine(path)
        self.assertIsNotNone(loaded)
        # current 事实一致
        cur = sorted(i.object for i in loaded.store.current_by_key(
            ("orderservice-p99-事件", "root_cause")))
        self.assertEqual(cur, ["连接池耗尽"])
        # 演化链与失效时间戳保留
        chain = loaded.store.history_of(("orderservice-p99-事件", "root_cause"))
        self.assertEqual(len(chain), 2)
        self.assertIsNotNone(chain[0].invalidated_at)
        # 引用链可解析（episode 原文回来了）
        ans = loaded.query("orderservice 事件的根因是什么")
        self.assertIn("更正", ans.citations[0].quote)
        # 审计日志条数一致
        self.assertEqual(len(loaded.export_audit()["resolutions"]),
                         len(eng.export_audit()["resolutions"]))

    def test_load_missing_returns_none(self):
        self.assertIsNone(load_engine("/nonexistent/mem.db"))


class TestOpsOntology(unittest.TestCase):
    def test_functional_set_from_registry(self):
        self.assertIn("root_cause", ops_functional_predicates())
        self.assertIn("has_alert", ops_functional_predicates())
        self.assertNotIn("mitigated_by", ops_functional_predicates())

    def test_ops_engine_supersede_rootcause(self):
        eng = ops_engine(extractor=DictExtractor([]))
        self.assertIsNotNone(eng.functional)          # 工厂已注入本体声明的函数型集合

    def test_ops_engine_multivalue_coexist(self):
        eng = ops_engine(extractor=DictExtractor([]))
        eng.add_facts([dict(memory_type="procedural", content="扩容连接池至 200",
                            subject="prod-orderservice", predicate="mitigated_by",
                            object="连接池扩容 200", valid_from="2026-09-20T09:45:00")],
                      ts="2026-09-20T09:45:00", origin="incident")
        eng.add_facts([dict(memory_type="procedural", content="回滚 v2.3.1",
                            subject="prod-orderservice", predicate="mitigated_by",
                            object="回滚 v2.3.1", valid_from="2026-09-20T10:00:00")],
                      ts="2026-09-20T10:00:00", origin="incident")
        cur = eng.store.current_by_key(("prod-orderservice", "mitigated_by"))
        self.assertEqual(len(cur), 2)   # 多值处置动作并存

    def test_extraction_prompt_contains_ontology(self):
        p = ops_extraction_prompt("BASE")
        self.assertIn("root_cause", p)
        self.assertIn("单值", p)
        self.assertIn("BASE", p)


if __name__ == "__main__":
    unittest.main()
