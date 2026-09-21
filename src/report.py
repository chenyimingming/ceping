"""报告生成：把评审结果渲染成 report.json / report.md / report.html / scores.csv。"""
import csv
import html
import io
import json
from pathlib import Path

METRIC_LABELS = {
    "accuracy": "准确性",
    "usefulness": "有用性",
    "tone": "语气与共情",
    "faithfulness": "忠实性",
    "strict_usefulness": "有用性（严格口径）",
}
LEVEL_TEXT = {"good": "好", "mixed": "一般", "poor": "差"}


def _fmt(value):
    number = float(value)
    text = "%.1f" % number
    return text[:-2] if text.endswith(".0") else text


def _chip(value):
    number = float(value)
    css = "good" if number >= 4.0 else ("mid" if number >= 3.0 else "bad")
    return '<span class="chip %s">%s</span>' % (css, _fmt(number))


def _bar(count, total, width=16):
    if not total:
        return ""
    filled = int(round(count / total * width))
    return "█" * filled


def suggestions(result):
    """返回 list：依据规则层特征生成的可执行改进建议（最多 4 条）。"""
    f = result.get("features") or {}
    items = []
    if result["metrics"]["faithfulness"].get("fabrication"):
        items.append("存在无依据的具体断言：删除或改为“以实际查询结果为准”，这是硬门槛问题。")
    if not f.get("proactive"):
        items.append("补充主动承接句式（如“我来帮您确认”“请提供订单号，我来处理”），替代“请您自行查看”。")
    if (f.get("difficulty") or f.get("complaint") or f.get("fault_report")) and not f.get("asks_info"):
        items.append("用户表达困难或情绪时，先追问具体卡点/订单信息，再给操作步骤。")
    if f.get("solution_after_steps"):
        items.append("把解决方案前置：先给可执行的解决路径，再列可选的自查步骤。")
    if f.get("internal_matter"):
        items.append("删除“加强培训/反馈给品控”等内部事项描述，改为对用户的直接承诺或补偿。")
    if f.get("multi_intent") and not (f.get("asks_info") or f.get("numbered_steps", 0) >= 2):
        items.append("用户一消息多诉求：逐项回应，避免只答一半。")
    if result.get("capability_needs"):
        items.append("系统能力缺口（%s）：要完全闭环需补对应能力，或在回复中明确转人工入口。" % "、".join(result["capability_needs"]))
    deduped = []
    for item in items:
        if item not in deduped:
            deduped.append(item)
    return deduped[:4]


def _distribution_rows(summary, metric):
    buckets = summary["metric_dists"][metric]
    total = sum(buckets.values())
    return [(name, count, _bar(count, total)) for name, count in buckets.items()]


def render_markdown(run):
    """返回 str：Markdown 版报告。"""
    meta = run["meta"]
    summary = run["summary"]
    cases = run["cases"]
    results = {r["case_id"]: r for r in run["results"]}
    weights = summary["weights"]
    lines = []
    add = lines.append

    add("# 客服自动回复质量评估报告（任务 0109）")
    add("")
    add("- 生成时间：%s（%s）" % (meta["generated_at"], meta.get("timezone", "Asia/Shanghai")))
    add("- 运行模式：%s（评审器：%s）" % (meta["mode"], meta["model"]))
    add("- 用例数：%d" % summary["n_cases"])
    add(
        "- 数据指纹：%s"
        % "，".join("%s=%s" % (name, digest) for name, digest in meta.get("data_fingerprint", {}).items())
    )
    add(
        "- 综合分权重：忠实性 %.0f%% / 准确性 %.0f%% / 有用性 %.0f%% / 语气 %.0f%%；命中硬门槛（编造或准确性<=2）时综合分封顶 %s"
        % (
            weights["faithfulness"] * 100,
            weights["accuracy"] * 100,
            weights["usefulness"] * 100,
            weights["tone"] * 100,
            _fmt(summary["gate_cap"]),
        )
    )
    add("")

    add("## 一、整体得分")
    add("")
    add("| 项目 | 数值 |")
    add("| --- | --- |")
    add("| 整体加权分（0-100） | %s |" % _fmt(summary["overall_mean"]))
    add("| 严格口径整体分（对照） | %s |" % _fmt(summary["overall_strict_mean"]))
    add("| 不合格条数（硬门槛） | %d |" % summary["failed_count"])
    for metric in ("accuracy", "usefulness", "tone", "faithfulness", "strict_usefulness"):
        add("| %s 均值（1-5） | %s |" % (METRIC_LABELS[metric], _fmt(summary["metric_means"][metric])))
    add("")

    add("## 二、各指标分布")
    for metric in ("accuracy", "usefulness", "tone", "faithfulness", "strict_usefulness"):
        add("")
        add("### %s" % METRIC_LABELS[metric])
        add("")
        add("| 分数段 | 条数 | 分布 |")
        add("| --- | --- | --- |")
        for name, count, bar in _distribution_rows(summary, metric):
            add("| %s | %d | %s |" % (name, count, bar))
    add("")

    add("## 三、最差 3 条及分析")
    for index, row in enumerate(summary["worst3"], start=1):
        case = cases[row["case_id"]]
        result = results[row["case_id"]]
        metrics = result["metrics"]
        add("")
        add("### %d. %s（综合分 %s / 严格口径 %s）" % (index, row["case_id"], _fmt(row["overall"]), _fmt(row["overall_strict"])))
        add("")
        add("- 用户问题：%s" % case["user_question"])
        add("- 自动回复：%s" % case["auto_reply"])
        add(
            "- 评分：准确性 %s｜有用性 %s｜语气 %s｜忠实性 %s｜严格口径有用性 %s"
            % (
                _fmt(metrics["accuracy"]["score"]),
                _fmt(metrics["usefulness"]["score"]),
                _fmt(metrics["tone"]["score"]),
                _fmt(metrics["faithfulness"]["score"]),
                _fmt(result["strict_usefulness"]),
            )
        )
        add("- 判定理由（有用性）：%s" % metrics["usefulness"]["reason"])
        add("- 关键证据（有用性）：%s" % metrics["usefulness"]["evidence"])
        add("- 改进建议：")
        for item in suggestions(result):
            add("  - %s" % item)
        add("- 人工参考：%s" % case["human_reference"])
        add("- 人工标注分析：%s" % case["annotator_notes"])
        if result.get("capability_needs"):
            add("- 能力缺口：%s" % "、".join(result["capability_needs"]))
    add("")

    validation = run.get("validation")
    add("## 四、与人工信号的一致性（验证评估方法）")
    add("")
    if validation:
        agreement = validation["usefulness_agreement"]
        add(
            "- 有用性逐条一致率：%d/%d（%.0f%%）"
            % (agreement["matched"], agreement["total"], agreement["rate"] * 100)
        )
        add("- 综合分与人工粗分的排序相关（Spearman）：%s" % validation["spearman_overall_vs_human"])
        add(
            "- 有用性分数与人工粗分的排序相关（Spearman，更同口径）：%s"
            % validation.get("spearman_usefulness_vs_human", "-")
        )
        add(
            "- 评审最差 5 条与人工“差评集合”的重合：%d/5"
            % validation["worst5"]["overlap"]
        )
        add("- 锚点用例：%d/%d 通过" % (validation["anchors_passed"], validation["anchors_total"]))
        add(
            "- 能力缺口覆盖：人工标注中 %d 条与能力相关，其中 %d 条被能力口径差异捕获"
            % (validation["capability_gap"]["dependent_cases"], validation["capability_gap"]["gap_detected"])
        )
        add("")
        add("| case | 评审有用性 | 评审档位 | 人工信号 | 是否一致 |")
        add("| --- | --- | --- | --- | --- |")
        for item in agreement["details"]:
            add(
                "| %s | %s | %s | %s | %s |"
                % (
                    item["case_id"],
                    _fmt(item["judge_score"]),
                    LEVEL_TEXT.get(item["judge_level"], item["judge_level"]),
                    LEVEL_TEXT.get(item["human_level"], item["human_level"]),
                    "是" if item["matched"] else "否",
                )
            )
        mismatches = [item["case_id"] for item in agreement["details"] if not item["matched"]]
        if mismatches:
            add("")
            add("- 不一致的 case：%s（具体原因见 README 的“局限性”一节）" % "、".join(mismatches))
    else:
        add("- 未找到人工信号文件（data/human_signals.json），跳过一致性校验。")
    add("")

    add("## 五、能力缺口清单（若补齐能力，哪些 case 可以完全闭环）")
    add("")
    add("| 能力 | 影响 case 数 | case 列表 |")
    add("| --- | --- | --- |")
    for cap_id, count in sorted(summary["capability_counts"].items(), key=lambda item: -item[1]):
        case_ids = [row["case_id"] for row in summary["rows"] if cap_id in row["capability_needs"]]
        add("| %s | %d | %s |" % (cap_id, count, "、".join(sorted(case_ids))))
    add("")

    add("## 六、全部 20 条明细")
    add("")
    add("| case | 准确性 | 有用性 | 语气 | 忠实性 | 严格有用性 | 综合分 | 不合格 | 能力缺口 |")
    add("| --- | --- | --- | --- | --- | --- | --- | --- | --- |")
    for row in summary["rows"]:
        add(
            "| %s | %s | %s | %s | %s | %s | %s | %s | %s |"
            % (
                row["case_id"],
                _fmt(row["accuracy"]),
                _fmt(row["usefulness"]),
                _fmt(row["tone"]),
                _fmt(row["faithfulness"]),
                _fmt(row["strict_usefulness"]),
                _fmt(row["overall"]),
                "是" if row["failed"] else "否",
                "、".join(row["capability_needs"]) or "-",
            )
        )
    add("")
    add("## 七、口径与方法说明")
    add("")
    add("- 主口径（能力边界内质量分）：按 capabilities.json 的能力清单打分，系统不具备的能力不要求自动回复执行。")
    add("- 严格口径（对照）：假设系统具备真人客服的全部查询与代办能力，未执行本可执行的动作会扣分。")
    add("- 评审方式：%s；盲评（提示词不含人工参考与标注分析）。" % ("规则基线 mock" if meta["mode"] == "mock" else "真实 LLM 评审"))
    if meta.get("cache_stats"):
        add("- 缓存：命中 %d 次（未重复计费）" % meta["cache_stats"].get("hits", 0))
    add("- 复现命令：`python run_eval.py --mode %s`" % meta["mode"])
    add("")
    return "\n".join(lines)


def render_html(run):
    """返回 str：单文件 HTML 报告（内置样式，便于截图与部署）。"""
    meta = run["meta"]
    summary = run["summary"]
    cases = run["cases"]
    results = {r["case_id"]: r for r in run["results"]}
    esc = html.escape
    parts = []
    add = parts.append

    add("<!DOCTYPE html><html lang='zh-CN'><head><meta charset='utf-8'>")
    add("<title>客服自动回复质量评估报告</title>")
    add(
        "<style>body{font-family:'Microsoft YaHei',system-ui,sans-serif;margin:0;background:#f5f6f8;color:#1f2329}"
        ".wrap{max-width:1080px;margin:0 auto;padding:24px 24px 60px}"
        "h1{font-size:24px;margin:8px 0}h2{font-size:19px;margin:34px 0 12px;border-left:4px solid #2f6fed;padding-left:10px}"
        ".meta{color:#6b7280;font-size:13px;line-height:1.8}"
        ".cards{display:flex;gap:12px;flex-wrap:wrap;margin:14px 0}"
        ".card{background:#fff;border-radius:10px;padding:14px 18px;box-shadow:0 1px 3px rgba(0,0,0,.08);min-width:168px}"
        ".card .num{font-size:26px;font-weight:700;color:#2f6fed}.card .lbl{font-size:12px;color:#6b7280;margin-top:4px}"
        "table{border-collapse:collapse;width:100%;background:#fff;border-radius:8px;overflow:hidden;margin:8px 0}"
        "th,td{border-bottom:1px solid #eceff3;padding:8px 10px;font-size:14px;text-align:left}"
        "th{background:#f0f3f8}"
        ".chip{display:inline-block;min-width:34px;text-align:center;padding:2px 7px;border-radius:6px;color:#fff;font-weight:600;font-size:13px}"
        ".good{background:#1f9d55}.mid{background:#d69e2e}.bad{background:#d64545}"
        ".bar{background:#e5e9f0;border-radius:4px;height:14px;min-width:120px}"
        ".bar>span{display:block;height:14px;background:#2f6fed;border-radius:4px}"
        ".case{background:#fff;border-radius:10px;padding:16px 18px;margin:14px 0;box-shadow:0 1px 3px rgba(0,0,0,.08)}"
        ".quote{background:#f7f9fc;border-left:3px solid #9db4d8;padding:8px 10px;margin:6px 0;font-size:14px;border-radius:0 6px 6px 0}"
        ".kv{font-size:14px;line-height:1.9}ul{margin:6px 0 6px 20px;padding:0}"
        ".tag{display:inline-block;background:#eef2fa;color:#2f6fed;border-radius:6px;padding:1px 8px;font-size:12px;margin-right:6px}"
        "</style></head><body><div class='wrap'>"
    )
    add("<h1>客服自动回复质量评估报告</h1>")
    add("<div class='meta'>")
    add("任务：0109 · 自动回复质量评估流水线<br>")
    add("生成时间：%s（%s）<br>" % (esc(meta["generated_at"]), esc(meta.get("timezone", "Asia/Shanghai"))))
    add("运行模式：%s（评审器：%s）<br>" % (esc(meta["mode"]), esc(meta["model"])))
    add("数据指纹：%s" % esc("，".join("%s=%s" % (n, d) for n, d in meta.get("data_fingerprint", {}).items())))
    add("</div>")

    means = summary["metric_means"]
    add("<div class='cards'>")
    add("<div class='card'><div class='num'>%s</div><div class='lbl'>整体加权分（0-100）</div></div>" % _fmt(summary["overall_mean"]))
    add("<div class='card'><div class='num'>%s</div><div class='lbl'>严格口径整体分（对照）</div></div>" % _fmt(summary["overall_strict_mean"]))
    add("<div class='card'><div class='num'>%d</div><div class='lbl'>不合格条数（硬门槛）</div></div>" % summary["failed_count"])
    for metric in ("accuracy", "usefulness", "tone", "faithfulness"):
        add("<div class='card'><div class='num'>%s</div><div class='lbl'>%s 均值（1-5）</div></div>" % (_fmt(means[metric]), METRIC_LABELS[metric]))
    add("</div>")

    add("<h2>一、各指标分布</h2>")
    add("<table><tr><th>指标</th><th>分布（分数段：条数）</th></tr>")
    for metric in ("accuracy", "usefulness", "tone", "faithfulness", "strict_usefulness"):
        chunks = []
        total = summary["n_cases"]
        for name, count in summary["metric_dists"][metric].items():
            percent = int(round(count / total * 100)) if total else 0
            chunks.append(
                "<div class='bar' style='margin:4px 0'><span style='width:%d%%'></span></div>"
                "<div class='meta'>%s 分：%d 条</div>" % (percent, esc(name), count)
            )
        add("<tr><td>%s</td><td>%s</td></tr>" % (METRIC_LABELS[metric], "".join(chunks)))
    add("</table>")

    add("<h2>二、最差 3 条及分析</h2>")
    for index, row in enumerate(summary["worst3"], start=1):
        case = cases[row["case_id"]]
        result = results[row["case_id"]]
        metrics = result["metrics"]
        add("<div class='case'>")
        add(
            "<h3>%d. %s <span class='meta'>综合分 %s / 严格口径 %s</span></h3>"
            % (index, esc(row["case_id"]), _fmt(row["overall"]), _fmt(row["overall_strict"]))
        )
        add("<div class='kv'><b>用户问题：</b>%s</div>" % esc(case["user_question"]))
        add("<div class='quote'><b>自动回复：</b>%s</div>" % esc(case["auto_reply"]))
        add(
            "<div class='kv'><b>评分：</b>准确性 %s 有用性 %s 语气 %s 忠实性 %s 严格口径有用性 %s</div>"
            % (
                _chip(metrics["accuracy"]["score"]),
                _chip(metrics["usefulness"]["score"]),
                _chip(metrics["tone"]["score"]),
                _chip(metrics["faithfulness"]["score"]),
                _chip(result["strict_usefulness"]),
            )
        )
        add("<div class='kv'><b>判定理由：</b>%s</div>" % esc(metrics["usefulness"]["reason"]))
        add("<div class='quote'><b>关键证据：</b>%s</div>" % esc(metrics["usefulness"]["evidence"]))
        add("<div class='kv'><b>改进建议：</b><ul>%s</ul></div>" % "".join("<li>%s</li>" % esc(item) for item in suggestions(result)))
        add("<div class='kv'><b>人工参考：</b>%s</div>" % esc(case["human_reference"]))
        add("<div class='kv'><b>人工标注分析：</b>%s</div>" % esc(case["annotator_notes"]))
        if result.get("capability_needs"):
            add("<div class='kv'><b>能力缺口：</b>%s</div>" % "".join("<span class='tag'>%s</span>" % esc(c) for c in result["capability_needs"]))
        add("</div>")

    validation = run.get("validation")
    add("<h2>三、与人工信号的一致性（验证评估方法）</h2>")
    if validation:
        agreement = validation["usefulness_agreement"]
        add("<div class='cards'>")
        add("<div class='card'><div class='num'>%d/%d</div><div class='lbl'>有用性逐条一致</div></div>" % (agreement["matched"], agreement["total"]))
        add(
            "<div class='card'><div class='num'>%s</div><div class='lbl'>排序相关：有用性 vs 人工（Spearman）</div></div>"
            % validation.get("spearman_usefulness_vs_human", "-")
        )
        add("<div class='card'><div class='num'>%d/5</div><div class='lbl'>最差 5 条重合</div></div>" % validation["worst5"]["overlap"])
        add("<div class='card'><div class='num'>%d/%d</div><div class='lbl'>锚点通过</div></div>" % (validation["anchors_passed"], validation["anchors_total"]))
        add("</div>")
        add("<table><tr><th>case</th><th>评审有用性</th><th>评审档位</th><th>人工信号</th><th>一致</th></tr>")
        for item in agreement["details"]:
            add(
                "<tr><td>%s</td><td>%s</td><td>%s</td><td>%s</td><td>%s</td></tr>"
                % (
                    esc(item["case_id"]),
                    _chip(item["judge_score"]),
                    esc(LEVEL_TEXT.get(item["judge_level"], item["judge_level"])),
                    esc(LEVEL_TEXT.get(item["human_level"], item["human_level"])),
                    "是" if item["matched"] else "<b>否</b>",
                )
            )
        add("</table>")
    else:
        add("<div class='meta'>未找到人工信号文件，跳过一致性校验。</div>")

    add("<h2>四、能力缺口清单</h2>")
    add("<table><tr><th>能力</th><th>影响 case 数</th><th>case 列表</th></tr>")
    for cap_id, count in sorted(summary["capability_counts"].items(), key=lambda item: -item[1]):
        case_ids = sorted(row["case_id"] for row in summary["rows"] if cap_id in row["capability_needs"])
        add("<tr><td>%s</td><td>%d</td><td>%s</td></tr>" % (esc(cap_id), count, esc("、".join(case_ids))))
    add("</table>")

    add("<h2>五、全部 20 条明细</h2>")
    add("<table><tr><th>case</th><th>准确性</th><th>有用性</th><th>语气</th><th>忠实性</th><th>严格有用性</th><th>综合分</th><th>不合格</th></tr>")
    for row in summary["rows"]:
        add(
            "<tr><td>%s</td><td>%s</td><td>%s</td><td>%s</td><td>%s</td><td>%s</td><td>%s</td><td>%s</td></tr>"
            % (
                esc(row["case_id"]),
                _chip(row["accuracy"]),
                _chip(row["usefulness"]),
                _chip(row["tone"]),
                _chip(row["faithfulness"]),
                _chip(row["strict_usefulness"]),
                _fmt(row["overall"]),
                "是" if row["failed"] else "否",
            )
        )
    add("</table>")

    add("<h2>六、口径说明</h2>")
    add(
        "<div class='kv'>"
        "主口径（能力边界内质量分）：按 capabilities.json 的能力清单打分，系统不具备的能力不要求自动回复执行。<br>"
        "严格口径（对照）：假设系统具备真人客服的全部查询与代办能力，未执行本可执行的动作会扣分。<br>"
        "评审方式：%s；盲评（提示词不含人工参考与标注分析）。<br>"
        "复现命令：<code>python run_eval.py --mode %s</code>"
        "</div>"
        % ("规则基线 mock" if meta["mode"] == "mock" else "真实 LLM 评审", esc(meta["mode"]))
    )
    add("</div></body></html>")
    return "".join(parts)


def render_csv(run):
    """返回 str：逐条分数明细（CSV）。"""
    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerow(
        ["case_id", "accuracy", "usefulness", "tone", "faithfulness", "strict_usefulness", "overall", "overall_strict", "failed", "capability_needs"]
    )
    for row in run["summary"]["rows"]:
        writer.writerow(
            [
                row["case_id"],
                _fmt(row["accuracy"]),
                _fmt(row["usefulness"]),
                _fmt(row["tone"]),
                _fmt(row["faithfulness"]),
                _fmt(row["strict_usefulness"]),
                _fmt(row["overall"]),
                _fmt(row["overall_strict"]),
                "1" if row["failed"] else "0",
                "|".join(row["capability_needs"]),
            ]
        )
    return buffer.getvalue()


def write_all(run, out_dir, mode):
    """把四种格式写入输出目录，返回 {格式: 路径}。"""
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    paths = {
        "JSON": out_dir / ("%s_report.json" % mode),
        "Markdown": out_dir / ("%s_report.md" % mode),
        "HTML": out_dir / ("%s_report.html" % mode),
        "CSV": out_dir / ("%s_scores.csv" % mode),
    }
    paths["JSON"].write_text(json.dumps(run, ensure_ascii=False, indent=2), encoding="utf-8")
    paths["Markdown"].write_text(render_markdown(run), encoding="utf-8")
    paths["HTML"].write_text(render_html(run), encoding="utf-8")
    paths["CSV"].write_text(render_csv(run), encoding="utf-8")
    return {key: str(path) for key, path in paths.items()}
