"""v2 第三轮验收：DEFER 复核 API + 向量召回三路融合。"""
import unittest

from nano_memory.extract import DictExtractor
from nano_memory.lifecycle import MemEngine
from nano_memory.review import list_pending, resolve_pending
from nano_memory.schema import Status
from nano_memory.vectors import VectorIndex


def _engine_with_pending():
    eng = MemEngine(extractor=DictExtractor([]))
    eng.add_facts([dict(memory_type="procedural", content="用户喜欢喝冰美式",
                        subject="用户", predicate="likes_drink", object="冰美式",
                        valid_from="2026-02-01")], ts="2026-02-01")
    # 迟到重提被顶掉的旧值 → resurrection → PENDING
    eng.add_facts([dict(memory_type="semantic", content="用户搬到了北京",
                        subject="用户", predicate="lives_in", object="北京",
                        valid_from="2026-06-01")], ts="2026-06-02")
    eng.add_facts([dict(memory_type="semantic", content="用户住在上海",
                        subject="用户", predicate="lives_in", object="上海",
                        valid_from="2026-01-10")], ts="2026-06-03")
    return eng


class TestReview(unittest.TestCase):
    def test_list_pending_with_context(self):
        eng = _engine_with_pending()
        pend = list_pending(eng)
        self.assertEqual(len(pend), 1)
        item, conflicts = pend[0]
        self.assertEqual(item.object, "上海")
        self.assertEqual(len(conflicts), 1)
        self.assertEqual(conflicts[0].object, "北京")

    def test_resolve_pending_supersede(self):
        eng = _engine_with_pending()
        promoted = resolve_pending(
            eng, lambda p, c: ("SUPERSEDE", "人工确认：用户又搬回上海"))
        self.assertEqual(len(promoted), 1)
        cur = eng.store.current_by_key(("用户", "lives_in"))
        self.assertEqual(len(cur), 1)
        self.assertEqual(cur[0].object, "上海")      # 复核后转正
        logs = eng.export_audit()["resolutions"]
        self.assertTrue(any(l["op"] == "SUPERSEDE" and l["judge"] == "manual"
                            for l in logs))

    def test_resolve_pending_reject(self):
        eng = _engine_with_pending()
        resolve_pending(eng, lambda p, c: ("REJECT", "迟到的复述，否决"))
        pend = list_pending(eng)
        self.assertEqual(len(pend), 0)
        cur = eng.store.current_by_key(("用户", "lives_in"))
        self.assertEqual(cur[0].object, "北京")      # 北京不受影响
        self.assertEqual(len(eng.export_audit()["resolutions"]), 4)  # ADD/ADD/DEFER/NOOP

    def test_pending_invisible_to_current_query(self):
        eng = _engine_with_pending()
        ans = eng.query("用户住在哪里")
        self.assertEqual(ans.items[0].object, "北京")
        self.assertNotIn("上海", {i.object for i in ans.items})  # PENDING 不入检索池


class TestVectors(unittest.TestCase):
    def test_tfidf_search(self):
        idx = VectorIndex().fit({
            "a": "用户住在上海", "b": "用户搬到了北京", "c": "用户喜欢喝冰美式"})
        hits = idx.search("用户住在哪里", k=3)
        self.assertEqual(hits[0][0], "a")            # 语义最近（共享 bigram 最多）
        self.assertGreater(hits[0][1], 0)

    def test_empty_index(self):
        self.assertEqual(VectorIndex().search("x", k=3), [])

    def test_rrf_fusion_smoke(self):
        eng = _engine_with_pending()
        ans = eng.query("用户住在哪里")
        self.assertTrue(ans.items)                   # 三路融合后仍可检索
        self.assertEqual(ans.items[0].object, "北京")


if __name__ == "__main__":
    unittest.main()
