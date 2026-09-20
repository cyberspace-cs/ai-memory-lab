"""验收语料 A：20 轮对话，含 3 处事实反转（换城市 / 换手机 / 改偏好）。"""
import unittest

from nano_memory.extract import DictExtractor, RuleExtractor
from nano_memory.lifecycle import MemEngine
from nano_memory.schema import Status


def build_engine() -> MemEngine:
    facts = [
        # —— 前期（1-3 月）——
        dict(memory_type="semantic", content="用户住在上海", subject="用户",
             predicate="lives_in", object="上海", valid_from="2026-01-10"),
        dict(memory_type="semantic", content="用户用 iPhone 15", subject="用户",
             predicate="uses_phone", object="iPhone 15", valid_from="2026-01-10"),
        dict(memory_type="procedural", content="用户喜欢喝冰美式", subject="用户",
             predicate="likes_drink", object="冰美式", valid_from="2026-02-01"),
        # —— 反转 1：换城市 ——
        dict(memory_type="semantic", content="用户搬到了北京", subject="用户",
             predicate="lives_in", object="北京", valid_from="2026-06-01"),
        # —— 正交事实（应 KEEP_BOTH）——
        dict(memory_type="semantic", content="用户在昆仑万维工作", subject="用户",
             predicate="works_at", object="昆仑万维", valid_from="2026-03-01"),
        # —— 重复事实（应 NOOP）——
        dict(memory_type="semantic", content="用户住在上海", subject="用户",
             predicate="lives_in", object="上海", valid_from="2026-01-10"),
        # —— 反转 2：换手机 ——
        dict(memory_type="semantic", content="用户换了小米 15", subject="用户",
             predicate="uses_phone", object="小米 15", valid_from="2026-07-15"),
        # —— 反转 3：改偏好（时间缺失 → DEFER_LLM）——
        dict(memory_type="procedural", content="用户现在喜欢喝拿铁", subject="用户",
             predicate="likes_drink", object="拿铁", valid_from=None),
    ]
    return MemEngine(extractor=DictExtractor(facts))


class TestCorpusA(unittest.TestCase):
    def setUp(self):
        self.eng = build_engine()
        self.eng.add_facts.__self__  # noqa: 触发属性检查
        # add_episode 语义：每条 dict 一个 episode 太碎，直接用 add_facts 分批注入
        # 为贴近真实使用，按时间分两批
        eng = MemEngine(extractor=self.eng.extractor)
        self.eng = eng
        self.eng.add_facts([dict(memory_type="semantic", content="用户住在上海",
                                 subject="用户", predicate="lives_in", object="上海",
                                 valid_from="2026-01-10"),
                            dict(memory_type="semantic", content="用户用 iPhone 15",
                                 subject="用户", predicate="uses_phone", object="iPhone 15",
                                 valid_from="2026-01-10"),
                            dict(memory_type="procedural", content="用户喜欢喝冰美式",
                                 subject="用户", predicate="likes_drink", object="冰美式",
                                 valid_from="2026-02-01")], ts="2026-03-01")
        self.eng.add_facts([dict(memory_type="semantic", content="用户搬到了北京",
                                 subject="用户", predicate="lives_in", object="北京",
                                 valid_from="2026-06-01"),
                            dict(memory_type="semantic", content="用户在昆仑万维工作",
                                 subject="用户", predicate="works_at", object="昆仑万维",
                                 valid_from="2026-03-01")], ts="2026-06-02")

    # —— 反转 1：换城市 ——
    def test_supersede_city(self):
        current = self.eng.store.current_by_key(("用户", "lives_in"))
        self.assertEqual(len(current), 1)
        self.assertEqual(current[0].object, "北京")
        old = self.eng.fact_history("用户", "lives_in")
        self.assertEqual(len(old), 2)                       # 旧事实永续
        self.assertEqual(old[0].status, Status.SUPERSEDED)
        self.assertEqual(old[0].valid_to, "2026-06-01")     # 不变量 2

    # —— 正交事实 ——
    def test_keep_both_orthogonal(self):
        current = self.eng.store.current_by_key(("用户", "works_at"))
        self.assertEqual(len(current), 1)
        self.assertEqual(current[0].object, "昆仑万维")

    # —— 不变量 3/4 ——
    def test_invariants(self):
        for key in {i.key for i in self.eng.store.items_including_history()}:
            cur = self.eng.store.current_by_key(key)
            self.assertLessEqual(len(cur), 1, f"invariant#3 broken for {key}")
        for log in self.eng.export_audit()["resolutions"]:
            self.assertTrue(log["evidence"].strip(), "invariant#4: evidence required")

    # —— 引用链 ——
    def test_provenance(self):
        ans = self.eng.query("用户住在哪里")
        self.assertTrue(ans.items)
        self.assertTrue(all(c.quote for c in ans.citations))
        self.assertEqual(ans.items[0].object, "北京")
        self.assertTrue(ans.items[0].source.episode_id)

    # —— 历史可查（'当时的真相'）——
    def test_history_query(self):
        ans = self.eng.query("用户住在哪里", include_history=True)
        objs = {i.object for i in ans.items}
        self.assertIn("上海", objs)
        self.assertIn("北京", objs)


class TestResolver(unittest.TestCase):
    def setUp(self):
        self.eng = MemEngine(extractor=DictExtractor([]))

    def _add(self, obj, vf):
        self.eng.add_facts([dict(memory_type="semantic", content=f"用户住在{obj}",
                                 subject="用户", predicate="lives_in", object=obj,
                                 valid_from=vf)], ts=vf)

    def test_noop_duplicate(self):
        self._add("上海", "2026-01-10")
        self._add("上海", "2026-01-10")
        ops = [l["op"] for l in self.eng.export_audit()["resolutions"]]
        self.assertEqual(ops, ["ADD", "NOOP"])
        # 重复不入库但累积印证（抄 graphiti duplicate→episodes 追加）
        cur = self.eng.store.current_by_key(("用户", "lives_in"))
        self.assertEqual(len(cur), 1)
        self.assertAlmostEqual(cur[0].confidence, 0.95)   # 0.9 + 0.05
        self.assertEqual(len(cur[0].corroborated_by), 1)

    def test_defer_llm_without_judge(self):
        self._add("上海", "2026-01-10")
        self.eng.add_facts([dict(memory_type="procedural",
                                 content="用户现在喜欢喝拿铁", subject="用户",
                                 predicate="likes_drink", object="拿铁",
                                 valid_from=None)], ts="2026-08-01")
        # 同键旧条目需要先存在才能触发 DEFER：补一条 likes_drink
        self.eng.add_facts([dict(memory_type="procedural", content="用户喜欢喝冰美式",
                                 subject="用户", predicate="likes_drink",
                                 object="冰美式", valid_from="2026-02-01")],
                           ts="2026-02-01")
        pend = [i for i in self.eng.store.items_including_history()
                if i.status == Status.PENDING]
        self.assertEqual(len(pend), 1)      # 缺时间的反转挂起待 LLM/人工

    def test_resurrection_defer(self):
        """「复活需复核」：迟到重提已被 supersede 的旧值 → PENDING，不得翻盘。"""
        self._add("上海", "2026-01-10")
        self._add("北京", "2026-06-01")          # 上海被顶掉
        self._add("上海", "2026-07-01")          # 迟到的复述
        cur = self.eng.store.current_by_key(("用户", "lives_in"))
        self.assertEqual(len(cur), 1)
        self.assertEqual(cur[0].object, "北京")  # 复活未遂，北京仍是 current
        ops = [l["op"] for l in self.eng.export_audit()["resolutions"]]
        self.assertIn("DEFER_LLM", ops)
        ev = [l["evidence"] for l in self.eng.export_audit()["resolutions"]
              if l["op"] == "DEFER_LLM"]
        self.assertTrue(any("resurrection" in e for e in ev))


class TestGraph(unittest.TestCase):
    def test_alias_and_path(self):
        from nano_memory.graph import EntityGraph
        g = EntityGraph(aliases={"kuaikun": "昆仑万维"})
        g.add_triple("用户", "works_at", "kuaikun")
        g.add_triple("昆仑万维", "hq_in", "北京")
        self.assertEqual(g.canonical("KUAIKUN"), "昆仑万维")
        self.assertEqual(g.path("用户", "北京"), ["用户", "昆仑万维", "北京"])
        self.assertIn("北京", g.multi_hop("用户", 2))


class TestRuleExtractor(unittest.TestCase):
    def test_chinese_patterns(self):
        ex = RuleExtractor()
        cands = ex.extract("我住在上海。我换新手机了，是小米15。我喜欢冰美式。",
                           ts="2026-07-01")
        triples = {(c.subject, c.predicate, c.object) for c in cands}
        self.assertIn(("我", "lives_in", "上海"), triples)
        self.assertIn(("我", "uses_phone", "小米15"), triples)
        self.assertIn(("我", "likes", "冰美式"), triples)


if __name__ == "__main__":
    unittest.main()
