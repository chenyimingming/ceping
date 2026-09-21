"""LLM 评审的单测：提示词构建（盲评）、请求封装、缓存、两种粒度与结构校验（全部离线）。"""
import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src import judge_llm, loader, rubric  # noqa: E402


class FakeResponse:
    def __init__(self, payload):
        self._data = json.dumps(payload, ensure_ascii=False).encode("utf-8")

    def read(self):
        return self._data

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False


class FakeTransport:
    """记录请求并依次返回预设 JSON 内容的假 HTTP 传输（替代真实网络调用）。"""

    def __init__(self, contents):
        if not isinstance(contents, list):
            contents = [contents]
        self.contents = list(contents)
        self.calls = []

    def __call__(self, request, timeout=None):
        self.calls.append(
            {
                "url": request.full_url,
                "body": json.loads(request.data.decode("utf-8")),
                "headers": dict(request.header_items()),
            }
        )
        content = self.contents[min(len(self.calls) - 1, len(self.contents) - 1)]
        payload = {"choices": [{"message": {"content": json.dumps(content, ensure_ascii=False)}}]}
        return FakeResponse(payload)


CASE_RESULT = {
    "accuracy": {"score": 5, "evidence": "额定能量不超过100Wh的充电宝可以随身携带上飞机", "reason": "与民航规定一致"},
    "usefulness": {
        "score": 3.5,
        "evidence": "请咨询具体航空公司",
        "reason": "给出通用规定，未针对具体商品",
        "sub": {"specificity": 3, "actionability": 3, "completeness": 4},
    },
    "tone": {"score": 4.5, "evidence": "您好", "reason": "礼貌专业"},
    "faithfulness": {
        "score": 5,
        "evidence": "100Wh",
        "reason": "未发现无依据断言",
        "fabrication": False,
        "unsupported_claims": [],
    },
    "strict_usefulness": 2.5,
    "capability_needs": ["product_lookup"],
    "gap_reason": "需要商品参数查询能力",
}

METRIC_CALLS = [
    {"score": 5, "evidence": "e1", "reason": "r1"},
    {
        "score": 3.5,
        "evidence": "e2",
        "reason": "r2",
        "sub": {"specificity": 3, "actionability": 3, "completeness": 4},
        "strict_usefulness": 2.5,
        "capability_needs": ["product_lookup"],
        "gap_reason": "g",
    },
    {"score": 4.5, "evidence": "e3", "reason": "r3"},
    {"score": 5, "evidence": "e4", "reason": "r4", "fabrication": False, "unsupported_claims": []},
]


class PromptTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.cases = {c["id"]: c for c in loader.load_cases(ROOT / "data")}
        cls.caps = loader.load_capabilities(ROOT / "capabilities.json")

    def test_prompt_is_blind_and_requests_json(self):
        case = self.cases["case_02"]
        messages = rubric.build_case_messages(case, self.caps)
        text = json.dumps(messages, ensure_ascii=False)
        self.assertIn("json", text.lower())
        self.assertNotIn(case["human_reference"], text)
        self.assertNotIn(case["annotator_notes"], text)
        self.assertIn("product_lookup", text)
        self.assertIn("strict_usefulness", text)

    def test_metric_messages_cover_single_metric(self):
        messages = rubric.build_metric_messages(self.cases["case_02"], self.caps, "tone")
        text = json.dumps(messages, ensure_ascii=False)
        self.assertIn("tone", text)
        self.assertNotIn("strict_usefulness", text)


class JudgeLlmTests(unittest.TestCase):
    def setUp(self):
        self.cases = {c["id"]: c for c in loader.load_cases(ROOT / "data")}
        self.caps = loader.load_capabilities(ROOT / "capabilities.json")
        self.config = {
            "base_url": "https://api.deepseek.com",
            "model": "deepseek-flash",
            "api_key": "sk-test",
            "temperature": 0.0,
            "timeout_seconds": 5,
            "max_retries": 0,
            "granularity": "case",
        }

    def test_case_mode_request_shape(self):
        transport = FakeTransport(CASE_RESULT)
        result = judge_llm.judge_case(
            self.cases["case_02"], self.config, self.caps, opener=transport, cache=None
        )
        call = transport.calls[0]
        self.assertTrue(call["url"].endswith("/chat/completions"))
        self.assertEqual("deepseek-flash", call["body"]["model"])
        self.assertEqual(0.0, call["body"]["temperature"])
        self.assertEqual({"type": "json_object"}, call["body"]["response_format"])
        self.assertEqual("Bearer sk-test", call["headers"].get("Authorization"))
        self.assertEqual("case_02", result["case_id"])
        self.assertEqual(3.5, result["metrics"]["usefulness"]["score"])
        self.assertIn("features", result)

    def test_cache_prevents_second_request(self):
        transport = FakeTransport(CASE_RESULT)
        cache = {}
        judge_llm.judge_case(self.cases["case_02"], self.config, self.caps, opener=transport, cache=cache)
        judge_llm.judge_case(self.cases["case_02"], self.config, self.caps, opener=transport, cache=cache)
        self.assertEqual(1, len(transport.calls))
        self.assertEqual(1, len(cache))

    def test_metric_mode_merges_four_calls(self):
        config = dict(self.config, granularity="metric")
        transport = FakeTransport(METRIC_CALLS)
        result = judge_llm.judge_case(
            self.cases["case_02"], config, self.caps, opener=transport, cache=None
        )
        self.assertEqual(4, len(transport.calls))
        self.assertEqual(5.0, result["metrics"]["accuracy"]["score"])
        self.assertEqual(3.5, result["metrics"]["usefulness"]["score"])
        self.assertEqual(2.5, result["strict_usefulness"])
        self.assertEqual(["product_lookup"], result["capability_needs"])

    def test_invalid_schema_raises(self):
        transport = FakeTransport({"score": 5})
        with self.assertRaises(ValueError):
            judge_llm.judge_case(self.cases["case_02"], self.config, self.caps, opener=transport, cache=None)

    def test_missing_api_key_raises(self):
        config = dict(self.config, api_key="")
        transport = FakeTransport(CASE_RESULT)
        with self.assertRaises(ValueError):
            judge_llm.judge_case(self.cases["case_02"], config, self.caps, opener=transport, cache=None)

    def test_normalize_clamps_scores(self):
        raw = dict(CASE_RESULT)
        raw["accuracy"] = dict(CASE_RESULT["accuracy"], score=9)
        result = judge_llm.normalize_result("case_02", raw, source="llm:test")
        self.assertEqual(5.0, result["metrics"]["accuracy"]["score"])


if __name__ == "__main__":
    unittest.main()
