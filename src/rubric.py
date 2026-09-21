"""评分细则与提示词构建。

- 四个指标：accuracy / usefulness / tone / faithfulness（1-5 分，允许 0.5 步进）；
- 两种口径：主口径（能力边界内质量）与严格口径（假设系统具备全部能力）；
- 盲评：提示词只包含"用户问题 + 自动回复 + 能力清单 + 评分细则"，
  不包含 human_ref 的人工参考与标注分析（避免用人工答案污染评分）。
"""

METRIC_KEYS = ("accuracy", "usefulness", "tone", "faithfulness")
METRIC_NAMES = {
    "accuracy": "准确性",
    "usefulness": "有用性",
    "tone": "语气与共情",
    "faithfulness": "忠实性（不瞎编）",
}

SYSTEM_PROMPT = (
    "你是一名严谨的客服质检专家，负责对“客服自动回复”逐条打分。"
    "你的输出必须是合法 JSON（json），不能包含 JSON 以外的任何文字。"
)

MAIN_BULLET = (
    "- 主口径（能力边界内质量）：只要求回复在“系统现有能力”范围内做到最好。"
    "系统不具备的能力（如查订单、查物流），回复没有执行不扣分；"
    "但回复本可以给出更清晰的路径、更主动的承接、更明确的兜底却未做到时，仍要扣分。"
)
STRICT_BULLET = (
    "- 严格口径（strict_usefulness）：假设系统具备真人客服的全部能力（可查订单/物流/商品/优惠券、"
    "可代用户操作），“没有帮用户做本可做的事”要扣分。"
)

METRIC_RUBRIC = {
    "accuracy": (
        "5=事实全部正确；4=基本正确但个别表述不严谨；3=部分正确或回避关键事实；"
        "2=存在明显错误；1=多处错误或误导。"
    ),
    "usefulness": (
        "5=直接回应具体问题并给出可执行下一步/主动承接；4=基本解决但略有遗漏；"
        "3=只给通用信息或通用流程，未针对本条问题；2=“正确但没用”，把本可解决的问题推回给用户；"
        "1=答非所问或让用户更困惑。子项：specificity（是否针对“这一个”问题）、"
        "actionability（可执行/主动承接程度）、completeness（信息不足时是否追问、多个问题是否逐项覆盖）。"
    ),
    "tone": (
        "5=礼貌专业且情绪匹配；4=礼貌得体；3=中性公文化、无温度；"
        "2=冷漠机械，或对情绪用户讲内部事项；1=冒犯或激化情绪。"
    ),
    "faithfulness": (
        "5=无任何无依据的具体断言；4=有轻微未经证实的表述；3=存在无法核验的具体断言；"
        "2=存在明显无依据的具体断言；1=编造关键事实或承诺。"
        "fabrication=true 仅当存在明显无依据的具体断言（2 分及以下）。"
    ),
}

CASE_OUTPUT = """【输出格式】只输出一个 JSON 对象（json），不要输出任何解释文字：
{
  "accuracy": {"score": 数字, "evidence": "回复中的原句片段", "reason": "不超过40字的理由"},
  "usefulness": {"score": 数字, "evidence": "原句片段", "reason": "不超过40字的理由", "sub": {"specificity": 数字, "actionability": 数字, "completeness": 数字}},
  "tone": {"score": 数字, "evidence": "原句片段", "reason": "不超过40字的理由"},
  "faithfulness": {"score": 数字, "evidence": "原句片段", "reason": "不超过40字的理由", "fabrication": true或false, "unsupported_claims": ["无依据的断言，没有则空数组"]},
  "strict_usefulness": 数字,
  "capability_needs": ["从能力清单的键中选，没有则为空数组"],
  "gap_reason": "一句话说明要让该问题完全闭环还缺什么能力；没有则为空字符串"
}
注意：evidence 必须逐字来自上面的自动回复；不确定时保守给分。"""

METRIC_OUTPUT = {
    "accuracy": '{"score": 数字, "evidence": "原句片段", "reason": "不超过40字的理由"}',
    "usefulness": (
        '{"score": 数字, "evidence": "原句片段", "reason": "不超过40字的理由", '
        '"sub": {"specificity": 数字, "actionability": 数字, "completeness": 数字}, '
        '"strict_usefulness": 数字, "capability_needs": ["..."], "gap_reason": "..."}'
    ),
    "tone": '{"score": 数字, "evidence": "原句片段", "reason": "不超过40字的理由"}',
    "faithfulness": (
        '{"score": 数字, "evidence": "原句片段", "reason": "不超过40字的理由", '
        '"fabrication": true或false, "unsupported_claims": ["..."]}'
    ),
}


def capabilities_text(capabilities):
    """返回 str：把能力清单渲染成提示词里的多行文本。"""
    lines = []
    for cid, info in capabilities.items():
        value = info.get("value")
        if value is True:
            state = "具备"
        elif value is False:
            state = "不具备"
        else:
            state = "未知"
        lines.append("- %s（%s）：%s" % (cid, info.get("desc", cid), state))
    return "\n".join(lines)


def _metric_anchors(metrics):
    return "\n".join("- %s（%s）：%s" % (name, metric, METRIC_RUBRIC[metric]) for metric, name in (
        (metric, METRIC_NAMES[metric]) for metric in metrics
    ))


def _preface(capabilities, metrics, include_strict):
    bullets = [MAIN_BULLET]
    if include_strict:
        bullets.append(STRICT_BULLET)
    return "【评分口径】\n%s\n\n【系统能力清单】\n%s\n\n【指标与 1-5 分锚点】（允许 0.5 步进）\n%s" % (
        "\n".join(bullets),
        capabilities_text(capabilities),
        _metric_anchors(metrics),
    )


def _case_body(case):
    return "【用户问题】\n%s\n\n【自动回复（被评估对象）】\n%s" % (case["user_question"], case["auto_reply"])


def build_case_messages(case, capabilities):
    """返回 list：case 粒度的提示词（一次调用评全部指标 + 严格口径）。"""
    user = "\n\n".join(
        [
            _preface(capabilities, METRIC_KEYS, include_strict=True),
            _case_body(case),
            CASE_OUTPUT,
        ]
    )
    return [{"role": "system", "content": SYSTEM_PROMPT}, {"role": "user", "content": user}]


def build_metric_messages(case, capabilities, metric):
    """返回 list：metric 粒度的提示词（一次调用只评一个指标）。"""
    user = "\n\n".join(
        [
            _preface(capabilities, (metric,), include_strict=(metric == "usefulness")),
            "【本次只评这一个指标】%s（%s）" % (METRIC_NAMES[metric], metric),
            _case_body(case),
            "【输出格式】只输出一个 JSON 对象（json）：\n" + METRIC_OUTPUT[metric],
        ]
    )
    return [{"role": "system", "content": SYSTEM_PROMPT}, {"role": "user", "content": user}]
