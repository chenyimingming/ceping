#!/usr/bin/env python3
"""客服自动回复质量评估流水线（任务 0109）的命令行入口。

用法：
  python run_eval.py --mode mock                       # 规则基线（离线、零成本、可复现）
  python run_eval.py --mode llm                        # 调用 DeepSeek（读 config.json）
  python run_eval.py --mode llm --list-models          # 查询账号下可用模型名
  python run_eval.py --mode llm --granularity metric   # 每指标一次调用
"""
import argparse
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src import aggregate, judge_llm, judge_mock, loader, report, validate  # noqa: E402
from src import config as config_module  # noqa: E402


def build_run(mode, model, results, summary, validation, capabilities, meta_extra):
    """返回 dict：报告渲染需要的完整运行数据（元信息 + 结果 + 汇总 + 校验 + 原始 case）。"""
    data_dir = ROOT / "data"
    fingerprint = {}
    for name in ("auto_replies.json", "human_ref.json", "human_signals.json"):
        path = data_dir / name
        if path.exists():
            fingerprint[name] = loader.hash_file(path)
    cases = {case["id"]: case for case in loader.load_cases(data_dir)}
    meta = {
        "mode": mode,
        "model": model,
        "generated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "timezone": "Asia/Shanghai",
        "data_fingerprint": fingerprint,
        "tool": "run_eval.py",
    }
    meta.update(meta_extra)
    return {
        "meta": meta,
        "summary": summary,
        "results": results,
        "cases": cases,
        "validation": validation,
        "capabilities": capabilities,
    }


def summary_text(run, paths):
    """返回 str：控制台摘要文本（同时用于终端打印与 outputs/*_console.txt）。"""
    summary = run["summary"]
    means = summary["metric_means"]
    lines = []
    lines.append("=" * 60)
    lines.append("客服自动回复质量评估（任务 0109）")
    lines.append("运行模式：%s（评审器：%s）" % (run["meta"]["mode"], run["meta"]["model"]))
    lines.append("用例数：%d" % summary["n_cases"])
    lines.append("整体加权分：%s / 100（严格口径对照：%s）" % (summary["overall_mean"], summary["overall_strict_mean"]))
    lines.append("不合格（硬门槛）：%d 条" % summary["failed_count"])
    lines.append(
        "各指标均值：准确性 %s | 有用性 %s | 语气 %s | 忠实性 %s"
        % (means["accuracy"], means["usefulness"], means["tone"], means["faithfulness"])
    )
    worst = "，".join("%s（%s 分）" % (row["case_id"], row["overall"]) for row in summary["worst3"])
    lines.append("最差 3 条：%s" % worst)
    if run.get("validation"):
        v = run["validation"]
        lines.append(
            "与人工信号一致性：%d/%d 条一致；最差 5 条重合 %d/5；锚点 %d/%d 通过"
            % (
                v["usefulness_agreement"]["matched"],
                v["usefulness_agreement"]["total"],
                v["worst5"]["overlap"],
                v["anchors_passed"],
                v["anchors_total"],
            )
        )
    if run["meta"].get("cache_stats"):
        stats = run["meta"]["cache_stats"]
        lines.append("LLM 缓存：命中 %d 次，新请求 %d 次" % (stats.get("hits", 0), stats.get("misses", 0)))
    lines.append("-" * 60)
    lines.append("报告文件：")
    for label, path in paths.items():
        lines.append("  %s: %s" % (label, path))
    lines.append("=" * 60)
    return "\n".join(lines)


def main(argv=None):
    try:
        sys.stdout.reconfigure(errors="replace")
    except Exception:
        pass
    parser = argparse.ArgumentParser(description="客服自动回复质量评估流水线（任务 0109）")
    parser.add_argument("--mode", choices=("mock", "llm"), default="mock", help="评审模式")
    parser.add_argument("--config", default=str(ROOT / "config.json"), help="LLM 模式配置文件")
    parser.add_argument("--data", default=str(ROOT / "data"), help="数据目录")
    parser.add_argument("--out", default=str(ROOT / "outputs"), help="报告输出目录")
    parser.add_argument("--capabilities", default=str(ROOT / "capabilities.json"), help="能力清单文件")
    parser.add_argument("--cache", default=str(ROOT / "cache" / "llm_cache.json"), help="LLM 结果缓存文件")
    parser.add_argument("--no-cache", action="store_true", help="忽略缓存重新调用")
    parser.add_argument("--granularity", choices=("case", "metric"), default=None, help="LLM 调用粒度")
    parser.add_argument("--list-models", action="store_true", help="查询账号可用模型名后退出")
    args = parser.parse_args(argv)

    if args.list_models:
        try:
            config = config_module.load_config(args.config)
            config_module.require_api_key(config)
            print("可用模型：")
            for name in judge_llm.list_models(config):
                print("  - " + name)
            return 0
        except (FileNotFoundError, ValueError) as exc:
            print("[提示] " + str(exc))
            return 1

    cases = loader.load_cases(args.data)
    capabilities = loader.load_capabilities(args.capabilities)
    cache_stats = {}
    if args.mode == "mock":
        results = judge_mock.judge_all(cases, capabilities)
        model = "mock-rules-v1"
        meta_extra = {"note": "规则基线：离线、零成本、可复现；用于与 LLM 评审对照"}
    else:
        try:
            config = config_module.load_config(args.config)
            config_module.require_api_key(config)
        except (FileNotFoundError, ValueError) as exc:
            print("[提示] " + str(exc))
            print("配置完成后重试：python run_eval.py --mode llm")
            return 1
        granularity = args.granularity or config.get("granularity", "case")
        results, cache_stats = judge_llm.judge_all(
            cases,
            config,
            capabilities,
            cache_path=args.cache,
            use_cache=not args.no_cache,
            granularity=granularity,
        )
        model = config["model"]
        meta_extra = {
            "base_url": config["base_url"],
            "granularity": granularity,
            "temperature": config.get("temperature"),
            "cache_stats": cache_stats,
        }

    summary = aggregate.aggregate(results)
    signals = loader.load_human_signals(args.data)
    validation = validate.validate(results, summary["rows"], signals) if signals else None
    run = build_run(args.mode, model, results, summary, validation, capabilities, meta_extra)
    paths = report.write_all(run, args.out, args.mode)
    console_path = Path(args.out) / ("%s_console.txt" % args.mode)
    text = summary_text(run, paths)
    console_path.write_text(text, encoding="utf-8")
    paths["控制台输出"] = str(console_path)
    print(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
