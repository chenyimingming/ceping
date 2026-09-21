"""汇总层：计算综合分（含硬门槛封顶）、指标分布与能力缺口清单。"""
from collections import Counter, OrderedDict

WEIGHTS = {"faithfulness": 0.30, "accuracy": 0.25, "usefulness": 0.30, "tone": 0.15}
GATE_CAP = 60.0
BUCKETS = ("1.0-1.9", "2.0-2.9", "3.0-3.9", "4.0-4.9", "5.0")


def overall_of(scores):
    """返回 float：把 1-5 分的指标分按权重折算成 0-100 的综合分。"""
    total = sum(WEIGHTS[key] * float(scores[key]) for key in WEIGHTS)
    return round(total / 5.0 * 100.0, 1)


def per_case_row(result):
    """返回 dict：单条 case 的分数行（综合分、严格口径综合分、硬门槛命中与缺口标记）。"""
    metrics = result["metrics"]
    scores = {key: float(metrics[key]["score"]) for key in WEIGHTS}
    failed = bool(metrics["faithfulness"].get("fabrication")) or scores["accuracy"] <= 2.0

    strict_scores = dict(scores)
    strict_scores["usefulness"] = float(result["strict_usefulness"])
    overall = overall_of(scores)
    overall_strict = overall_of(strict_scores)
    if failed:
        overall = min(overall, GATE_CAP)
        overall_strict = min(overall_strict, GATE_CAP)

    return {
        "case_id": result["case_id"],
        "accuracy": scores["accuracy"],
        "usefulness": scores["usefulness"],
        "tone": scores["tone"],
        "faithfulness": scores["faithfulness"],
        "strict_usefulness": strict_scores["usefulness"],
        "overall": overall,
        "overall_strict": overall_strict,
        "failed": failed,
        "capability_needs": list(result.get("capability_needs", [])),
        "gap": float(result["strict_usefulness"]) < scores["usefulness"] - 0.01,
    }


def metric_distribution(rows, metric):
    """返回 OrderedDict：指定指标在 5 个分数段上的计数。"""
    buckets = OrderedDict((name, 0) for name in BUCKETS)
    for row in rows:
        value = float(row[metric])
        if value >= 5.0:
            buckets["5.0"] += 1
        elif value >= 4.0:
            buckets["4.0-4.9"] += 1
        elif value >= 3.0:
            buckets["3.0-3.9"] += 1
        elif value >= 2.0:
            buckets["2.0-2.9"] += 1
        else:
            buckets["1.0-1.9"] += 1
    return buckets


def aggregate(results):
    """返回 dict：全部 case 的汇总（均值、分布、最差 3 条、缺口清单与统计）。"""
    rows = [per_case_row(result) for result in results]
    metrics = ("accuracy", "usefulness", "tone", "faithfulness", "strict_usefulness")
    metric_means = {
        metric: round(sum(float(row[metric]) for row in rows) / len(rows), 2) if rows else 0.0
        for metric in metrics
    }
    metric_dists = {metric: metric_distribution(rows, metric) for metric in metrics}
    worst = sorted(rows, key=lambda r: (r["overall"], r["usefulness"], r["case_id"]))[:3]
    gap_cases = [row for row in rows if row["gap"]]
    capability_counter = Counter()
    for row in rows:
        capability_counter.update(row["capability_needs"])

    return {
        "n_cases": len(rows),
        "weights": WEIGHTS,
        "gate_cap": GATE_CAP,
        "rows": rows,
        "overall_mean": round(sum(row["overall"] for row in rows) / len(rows), 1) if rows else 0.0,
        "overall_strict_mean": round(sum(row["overall_strict"] for row in rows) / len(rows), 1) if rows else 0.0,
        "failed_count": sum(1 for row in rows if row["failed"]),
        "metric_means": metric_means,
        "metric_dists": metric_dists,
        "worst3": worst,
        "gap_cases": gap_cases,
        "capability_counts": dict(capability_counter),
    }
