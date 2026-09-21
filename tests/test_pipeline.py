"""流水线单测：mock 评审输出结构、锚点表现、汇总统计、硬门槛与人工信号校验。"""
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src import aggregate, judge_mock, loader, validate  # noqa: E402

CAPS = {
    "order_lookup": {"value": False, "desc": "查询用户订单状态/详情"},
    "logistics_lookup": {"value": False, "desc": "查询物流轨迹"},
    "refund_lookup": {"value": False, "desc": "查询退款进度"},
    "product_lookup": {"value": False, "desc": "查询商品参数/成分/材质"},
    "coupon_lookup": {"value": False, "desc": "查询优惠券可用状态"},
    "inventory_lookup": {"value": False, "desc": "查询补货计划/库存"},
    "account_lookup": {"value": False, "desc": "查询账号登录记录"},
    "after_sales_action": {"value": False, "desc": "代用户执行退换/取消等操作"},
    "product_comparison": {"value": False, "desc": "按用户需求对比推荐商品"},
    "video_feature": {"value": False, "desc": "提供商品实物视频"},
}


class MockJudgeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.cases = loader.load_cases(ROOT / "data")
        cls.results = judge_mock.judge_all(cls.cases, CAPS)
        cls.by_id = {r["case_id"]: r for r in cls.results}

    def test_schema_and_score_ranges(self):
        self.assertEqual(20, len(self.results))
        for result in self.results:
            for metric in ("accuracy", "usefulness", "tone", "faithfulness"):
                score = result["metrics"][metric]["score"]
                self.assertGreaterEqual(score, 1.0)
                self.assertLessEqual(score, 5.0)
                self.assertTrue(result["metrics"][metric]["reason"])
            self.assertIn("sub", result["metrics"]["usefulness"])
            self.assertIn("fabrication", result["metrics"]["faithfulness"])
            self.assertGreaterEqual(result["strict_usefulness"], 1.0)
            self.assertLessEqual(result["strict_usefulness"], 5.0)

    def test_anchor_case08_and_case20_usefulness_low(self):
        self.assertLess(self.by_id["case_08"]["metrics"]["usefulness"]["score"], 3.0)
        self.assertLess(self.by_id["case_20"]["metrics"]["usefulness"]["score"], 3.0)

    def test_anchor_case05_not_better_than_ok(self):
        self.assertLessEqual(self.by_id["case_05"]["metrics"]["usefulness"]["score"], 3.0)

    def test_anchor_case14_is_acceptable(self):
        self.assertGreaterEqual(self.by_id["case_14"]["metrics"]["usefulness"]["score"], 3.5)

    def test_accuracy_high_on_policy_cases(self):
        for case_id in ("case_02", "case_03", "case_09", "case_10", "case_18"):
            self.assertGreaterEqual(
                self.by_id[case_id]["metrics"]["accuracy"]["score"], 4.0, msg=case_id
            )

    def test_no_fabrication_in_dataset(self):
        for result in self.results:
            self.assertFalse(result["metrics"]["faithfulness"]["fabrication"], msg=result["case_id"])

    def test_capability_gap_for_case03(self):
        result = self.by_id["case_03"]
        self.assertIn("refund_lookup", result["capability_needs"])
        self.assertLess(result["strict_usefulness"], result["metrics"]["usefulness"]["score"])


class AggregateTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.cases = loader.load_cases(ROOT / "data")
        cls.results = judge_mock.judge_all(cls.cases, CAPS)
        cls.summary = aggregate.aggregate(cls.results)

    def test_rows_and_worst3(self):
        self.assertEqual(20, len(self.summary["rows"]))
        self.assertEqual(3, len(self.summary["worst3"]))
        for row in self.summary["rows"]:
            self.assertGreaterEqual(row["overall"], 0.0)
            self.assertLessEqual(row["overall"], 100.0)

    def test_metric_distribution_counts_sum_to_20(self):
        for metric, buckets in self.summary["metric_dists"].items():
            self.assertEqual(20, sum(buckets.values()), msg=metric)

    def test_gate_caps_failed_case(self):
        synthetic = {
            "case_id": "case_x",
            "source": "test",
            "metrics": {
                "accuracy": {"score": 5, "evidence": "", "reason": ""},
                "usefulness": {"score": 5, "evidence": "", "reason": "", "sub": {}},
                "tone": {"score": 5, "evidence": "", "reason": ""},
                "faithfulness": {"score": 2, "evidence": "", "reason": "", "fabrication": True},
            },
            "strict_usefulness": 5,
            "capability_needs": [],
        }
        row = aggregate.per_case_row(synthetic)
        self.assertTrue(row["failed"])
        self.assertLessEqual(row["overall"], 60.0)


class ValidateTests(unittest.TestCase):
    def test_agreement_and_anchors(self):
        cases = loader.load_cases(ROOT / "data")
        results = judge_mock.judge_all(cases, CAPS)
        summary = aggregate.aggregate(results)
        signals = {
            "case_01": {"usefulness": "poor"},
            "case_08": {"usefulness": "poor"},
            "case_14": {"usefulness": "good"},
            "case_09": {"usefulness": "mixed"},
        }
        report = validate.validate(results, summary["rows"], signals)
        self.assertEqual(4, report["usefulness_agreement"]["total"])
        self.assertGreaterEqual(report["usefulness_agreement"]["matched"], 3)
        self.assertEqual(8, report["anchors_total"])
        self.assertGreaterEqual(report["anchors_passed"], 6)


if __name__ == "__main__":
    unittest.main()
