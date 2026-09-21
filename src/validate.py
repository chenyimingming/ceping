"""校验层：把评审结果与"人工信号"（从标注分析推导的粗标签）做一致性对比，并检查锚点用例。"""

LEVEL_VALUE = {"poor": 1, "mixed": 2, "good": 3}

ANCHORS = [
    {"case_id": "case_08", "metric": "usefulness", "op": "<", "threshold": 3.0},
    {"case_id": "case_20", "metric": "usefulness", "op": "<", "threshold": 3.0},
    {"case_id": "case_02", "metric": "accuracy", "op": ">=", "threshold": 4.0},
    {"case_id": "case_03", "metric": "accuracy", "op": ">=", "threshold": 4.0},
    {"case_id": "case_09", "metric": "accuracy", "op": ">=", "threshold": 4.0},
    {"case_id": "case_10", "metric": "accuracy", "op": ">=", "threshold": 4.0},
    {"case_id": "case_18", "metric": "accuracy", "op": ">=", "threshold": 4.0},
    {"case_id": "case_14", "metric": "usefulness", "op": ">=", "threshold": 3.5},
]


def score_to_level(score):
    """返回 str：把 1-5 分映射成 poor / mixed / good 三档。"""
    if score >= 4.0:
        return "good"
    if score >= 3.0:
        return "mixed"
    return "poor"


def _average_ranks(values):
    order = sorted(range(len(values)), key=lambda i: values[i])
    ranks = [0.0] * len(values)
    i = 0
    while i < len(order):
        j = i
        while j + 1 < len(order) and values[order[j + 1]] == values[order[i]]:
            j += 1
        rank = (i + j) / 2.0 + 1.0
        for k in range(i, j + 1):
            ranks[order[k]] = rank
        i = j + 1
    return ranks


def _pearson(xs, ys):
    n = len(xs)
    if n < 2:
        return 0.0
    mx = sum(xs) / n
    my = sum(ys) / n
    num = sum((x - mx) * (y - my) for x, y in zip(xs, ys))
    dx = sum((x - mx) ** 2 for x in xs) ** 0.5
    dy = sum((y - my) ** 2 for y in ys) ** 0.5
    if dx == 0 or dy == 0:
        return 0.0
    return num / (dx * dy)


def spearman(xs, ys):
    """返回 float：两列数值的 Spearman 排序相关系数（并列名次取平均）。"""
    return round(_pearson(_average_ranks(xs), _average_ranks(ys)), 3)


def validate(results, rows, signals):
    """返回 dict：一致性（逐条匹配率、排序相关、最差名单重叠）、锚点结果与能力缺口覆盖。"""
    by_id = {r["case_id"]: r for r in results}
    row_by_id = {r["case_id"]: r for r in rows}

    matched = 0
    details = []
    for case_id, sig in sorted(signals.items()):
        if case_id not in by_id or not sig.get("usefulness"):
            continue
        score = float(by_id[case_id]["metrics"]["usefulness"]["score"])
        judge_level = score_to_level(score)
        human_level = sig["usefulness"]
        ok = judge_level == human_level
        matched += 1 if ok else 0
        details.append(
            {
                "case_id": case_id,
                "judge_score": score,
                "judge_level": judge_level,
                "human_level": human_level,
                "matched": ok,
            }
        )
    total = len(details)

    rows_sorted = sorted(rows, key=lambda r: (r["overall"], r["usefulness"], r["case_id"]))
    worst5 = [r["case_id"] for r in rows_sorted[:5]]
    poor_set = {cid for cid, s in signals.items() if s.get("usefulness") == "poor"}

    xs, ys = [], []
    for case_id, sig in sorted(signals.items()):
        if case_id in row_by_id and sig.get("usefulness") in LEVEL_VALUE:
            xs.append(row_by_id[case_id]["overall"])
            ys.append(LEVEL_VALUE[sig["usefulness"]])
    rho = spearman(xs, ys) if len(xs) >= 2 else 0.0

    # 与人工信号更同口径的对比：用"有用性分数"（人工标注主要针对有用性）
    xs_useful, ys_useful = [], []
    for case_id, sig in sorted(signals.items()):
        if case_id in by_id and sig.get("usefulness") in LEVEL_VALUE:
            xs_useful.append(float(by_id[case_id]["metrics"]["usefulness"]["score"]))
            ys_useful.append(LEVEL_VALUE[sig["usefulness"]])
    rho_usefulness = spearman(xs_useful, ys_useful) if len(xs_useful) >= 2 else 0.0

    anchors = []
    passed = 0
    for anchor in ANCHORS:
        result = by_id.get(anchor["case_id"])
        if not result:
            continue
        value = float(result["metrics"][anchor["metric"]]["score"])
        ok = {
            "<": value < anchor["threshold"],
            "<=": value <= anchor["threshold"],
            ">=": value >= anchor["threshold"],
        }[anchor["op"]]
        passed += 1 if ok else 0
        anchors.append({**anchor, "value": value, "passed": ok})

    dependent = [cid for cid, s in signals.items() if s.get("capability_dependent")]
    gap_hit = [cid for cid in dependent if row_by_id.get(cid, {}).get("gap")]

    return {
        "n_signals": len(signals),
        "usefulness_agreement": {
            "matched": matched,
            "total": total,
            "rate": round(matched / total, 3) if total else 0.0,
            "details": details,
        },
        "spearman_overall_vs_human": rho,
        "spearman_usefulness_vs_human": rho_usefulness,
        "worst5": {
            "judge_worst5": worst5,
            "human_poor_set": sorted(poor_set),
            "overlap": len(set(worst5) & poor_set),
        },
        "anchors": anchors,
        "anchors_passed": passed,
        "anchors_total": len(anchors),
        "capability_gap": {
            "dependent_cases": len(dependent),
            "gap_detected": len(gap_hit),
            "case_ids": sorted(gap_hit),
        },
    }
