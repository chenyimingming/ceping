"""规则层特征的单测：用任务数据中的真实案例验证特征抽取行为。"""
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src import features, loader  # noqa: E402


class FeatureTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.cases = {c["id"]: c for c in loader.load_cases(ROOT / "data")}

    def test_20_cases_loaded(self):
        self.assertEqual(20, len(self.cases))

    def test_case08_is_case_specific_and_redirects_to_self_service(self):
        f = features.extract_features(self.cases["case_08"])
        self.assertTrue(f["case_specific"])
        self.assertTrue(f["self_service"])
        self.assertFalse(f["proactive"])

    def test_case20_has_difficulty_and_steps_but_no_followup_question(self):
        f = features.extract_features(self.cases["case_20"])
        self.assertTrue(f["difficulty"])
        self.assertGreaterEqual(f["numbered_steps"], 2)
        self.assertFalse(f["asks_info"])

    def test_case05_contains_internal_matter_and_restate_ask(self):
        f = features.extract_features(self.cases["case_05"])
        self.assertTrue(f["complaint"])
        self.assertTrue(f["internal_matter"])
        self.assertTrue(f["restate_ask"])

    def test_case03_has_numeric_claims_and_no_case_specific_marker(self):
        f = features.extract_features(self.cases["case_03"])
        self.assertTrue(f["numeric_claims"])
        self.assertFalse(f["case_specific"])

    def test_case17_is_multi_intent(self):
        f = features.extract_features(self.cases["case_17"])
        self.assertTrue(f["multi_intent"])

    def test_kb_violation_detected_on_synthetic_reply(self):
        case = {"user_question": "充电宝能带吗", "auto_reply": "超过100Wh的充电宝也可以随身带上飞机。"}
        f = features.extract_features(case)
        self.assertTrue(f["kb_violations"])

    def test_positive_100wh_statement_is_not_a_violation(self):
        case = {"user_question": "充电宝能带吗", "auto_reply": "额定能量不超过100Wh的充电宝可以随身携带上飞机。"}
        f = features.extract_features(case)
        self.assertEqual([], f["kb_violations"])

    def test_dataset_has_no_kb_violations(self):
        for case in self.cases.values():
            f = features.extract_features(case)
            self.assertEqual([], f["kb_violations"], msg=case["id"])


if __name__ == "__main__":
    unittest.main()
