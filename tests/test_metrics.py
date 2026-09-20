"""评测指标验收：语料 A + 语料 B 全部指标应达 100%。"""
import unittest

from nano_memory.extract import DictExtractor
from nano_memory.lifecycle import MemEngine
from nano_memory.metrics import evaluate

from test_memory import build_engine as build_corpus_a  # noqa: 相对导入由 discover 处理
from test_incident_corpus import build_engine as build_corpus_b


class TestMetrics(unittest.TestCase):
    def test_corpus_a_metrics(self):
        eng = MemEngine(extractor=DictExtractor([]))
        eng.add_facts([
            dict(memory_type="semantic", content="用户住在上海", subject="用户",
                 predicate="lives_in", object="上海", valid_from="2026-01-10"),
            dict(memory_type="semantic", content="用户用 iPhone 15", subject="用户",
                 predicate="uses_phone", object="iPhone 15", valid_from="2026-01-10"),
            dict(memory_type="procedural", content="用户喜欢喝冰美式", subject="用户",
                 predicate="likes_drink", object="冰美式", valid_from="2026-02-01"),
        ], ts="2026-03-01")
        eng.add_facts([dict(memory_type="semantic", content="用户搬到了北京",
                            subject="用户", predicate="lives_in", object="北京",
                            valid_from="2026-06-01")], ts="2026-06-02")
        # 重复陈述仍在任的事实（触发 NOOP 印证）；重提旧值会走 stale-DEFER，是另一种路径
        eng.add_facts([dict(memory_type="semantic", content="用户用 iPhone 15",
                            subject="用户", predicate="uses_phone", object="iPhone 15",
                            valid_from="2026-01-10")], ts="2026-06-03")
        rep = evaluate(
            eng,
            expect_current={("用户", "lives_in"): ["北京"],
                            ("用户", "uses_phone"): ["iPhone 15"],
                            ("用户", "likes_drink"): ["冰美式"]},
            expect_history={("用户", "lives_in"): {"上海", "北京"}},
        )
        self.assertEqual(rep.current_accuracy, 1.0)
        self.assertEqual(rep.supersede_correctness, 1.0)
        self.assertEqual(rep.citation_resolvability, 1.0)
        self.assertGreater(rep.corroboration_coverage, 0.0)
        self.assertFalse(rep.details, rep.details)

    def test_corpus_b_metrics(self):
        eng = MemEngine(extractor=DictExtractor([]))
        eng.add_facts([dict(memory_type="episodic",
                            content="prod-orderservice p99 延迟 3.2s，触发告警 latency-high",
                            subject="prod-orderservice", predicate="has_alert",
                            object="p99 延迟 3.2s", valid_from="2026-09-20T09:00:00")],
                      ts="2026-09-20T09:00:00", origin="incident")
        eng.add_facts([dict(memory_type="semantic",
                            content="初判：下游 pay-service 超时引发 orderservice 延迟雪崩",
                            subject="orderservice-p99-事件", predicate="root_cause",
                            object="pay-service 超时", valid_from="2026-09-20T09:15:00")],
                      ts="2026-09-20T09:15:00", origin="incident")
        eng.add_facts([dict(memory_type="semantic",
                            content="更正：根因是 orderservice 连接池耗尽，pay-service 超时是结果",
                            subject="orderservice-p99-事件", predicate="root_cause",
                            object="连接池耗尽", valid_from="2026-09-20T09:40:00")],
                      ts="2026-09-20T09:40:00", origin="incident")
        rep = evaluate(
            eng,
            expect_current={("orderservice-p99-事件", "root_cause"): ["连接池耗尽"],
                            ("prod-orderservice", "has_alert"): ["p99 延迟 3.2s"]},
            expect_history={("orderservice-p99-事件", "root_cause"):
                            {"pay-service 超时", "连接池耗尽"}},
        )
        self.assertEqual(rep.current_accuracy, 1.0)
        self.assertEqual(rep.supersede_correctness, 1.0)
        self.assertEqual(rep.citation_resolvability, 1.0)
        self.assertFalse(rep.details, rep.details)


if __name__ == "__main__":
    unittest.main()
