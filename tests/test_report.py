"""报告生成冒烟测试：四种格式可渲染且包含关键段落。"""
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src import aggregate, judge_mock, loader, report, validate  # noqa: E402


class ReportTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cases = loader.load_cases(ROOT / "data")
        capabilities = loader.load_capabilities(ROOT / "capabilities.json")
        results = judge_mock.judge_all(cases, capabilities)
        summary = aggregate.aggregate(results)
        signals = loader.load_human_signals(ROOT / "data")
        validation = validate.validate(results, summary["rows"], signals)
        cls.payload = {
            "meta": {
                "mode": "mock",
                "model": "mock-rules-v1",
                "generated_at": "2026-09-21 00:00:00",
                "timezone": "Asia/Shanghai",
                "data_fingerprint": {"auto_replies.json": "test"},
            },
            "summary": summary,
            "results": results,
            "cases": {c["id"]: c for c in cases},
            "validation": validation,
            "capabilities": capabilities,
        }

    def test_markdown_contains_required_sections(self):
        text = report.render_markdown(self.payload)
        for keyword in ("整体得分", "各指标分布", "最差 3 条", "与人工信号的一致性", "能力缺口清单"):
            self.assertIn(keyword, text)

    def test_html_and_csv_render(self):
        html = report.render_html(self.payload)
        self.assertIn("<!DOCTYPE html>", html)
        self.assertIn("最差 3 条", html)
        csv_text = report.render_csv(self.payload)
        self.assertEqual(21, len(csv_text.strip().splitlines()))

    def test_write_all_outputs_four_files(self):
        with tempfile.TemporaryDirectory() as tmp:
            paths = report.write_all(self.payload, tmp, "mock")
            self.assertEqual(4, len(paths))
            for path in paths.values():
                self.assertTrue(Path(path).exists(), msg=path)


if __name__ == "__main__":
    unittest.main()
