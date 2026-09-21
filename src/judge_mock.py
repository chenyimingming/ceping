"""mock 评审器：用规则层特征按与 LLM 评审相同的结构给回复打分（确定性、可复现、零成本）。

注意：mock 只覆盖"可写成规则"的信号，无法真正识别细粒度语义（如"答非所问"的隐含语义），
因此它既是无网络环境下的兜底，也是与 LLM 评审对照的基线；其结论会与人工信号做一致性校验。
"""
from . import features

SOURCE = "mock-rules-v1"


def _clamp(value, lo=1.0, hi=5.0):
    return max(lo, min(hi, value))


def _half(value):
    return round(value * 2) / 2


def _capability_needs(f, capabilities):
    return [cid for cid in f["capability_hits"] if capabilities.get(cid, {}).get("value") is not True]


def _gap_reason(needs, capabilities):
    if not needs:
        return ""
    names = "、".join(capabilities.get(n, {}).get("desc", n) for n in needs)
    return "要让该问题完全闭环，需要系统能力：" + names


def judge_case(case, capabilities):
    """返回 dict：单条 case 的四指标评分 + 严格口径参照 + 能力缺口标注（结构与 LLM 评审一致）。"""
    f = features.extract_features(case)
    reply = case["auto_reply"]
    needs = _capability_needs(f, capabilities)
    redirect_first = (
        f["self_service"]
        and f["proactive"]
        and reply.find(f["self_service_word"]) != -1
        and reply.find(f["self_service_word"]) < reply.find(f["proactive_word"])
    )

    if f["kb_violations"]:
        accuracy = 3.0
        accuracy_reason = "存在与最小知识库冲突的断言：" + "；".join(v["desc"] for v in f["kb_violations"])
    else:
        accuracy = 5.0
        accuracy_reason = "未发现与最小知识库冲突的事实断言（规则层只覆盖少量高风险口径）。"
    accuracy_evidence = reply[:44] + ("…" if len(reply) > 44 else "")

    notes = []
    if f["feedback"]:
        usefulness = 4.0 if (f["apology"] or f["polite"]) else 3.0
        notes.append("建议类反馈：按“感谢+承诺转达”处理")
    else:
        usefulness = 3.0
        notes.append("基线 3.0（信息型回答）")
        if f["proactive"]:
            usefulness += 0.5 if redirect_first else 1.0
            notes.append("承接出现在自助提示之后（只算兜底）+0.5" if redirect_first else "主动承接 +1.0")
        if f["asks_info"]:
            usefulness += 0.5
            notes.append("追问关键信息 +0.5")
        if f["numbered_steps"] >= 2:
            usefulness += 0.5
            notes.append("分步操作 +0.5")
        if f["numeric_claims"]:
            usefulness += 0.5
            notes.append("给出具体时效/数值 +0.5")
        if f["self_service"] and not f["proactive"] and f["case_specific"]:
            usefulness -= 1.0
            notes.append("个案问题未主动承接、导向自助 -1.0")
        elif redirect_first:
            usefulness -= 1.0
            notes.append("先导向自助、承接只是兜底 -1.0")
        elif f["self_service"] and f["proactive"]:
            usefulness -= 0.5
            notes.append("同时给出自助路径 -0.5")
        misaim = (
            (f["difficulty"] or f["complaint"] or f["fault_report"])
            and not f["proactive"]
            and not f["asks_info"]
            and not f["direct_solution"]
        )
        if misaim:
            usefulness -= 1.5
            notes.append("用户有困难/情绪但回复既未追问、也未给直接方案 -1.5")
        if f["solution_after_steps"]:
            usefulness -= 1.0
            notes.append("先让用户自查、解决方案后置 -1.0")
        if f["internal_matter"] and (f["complaint"] or f["fault_report"]):
            usefulness -= 1.0
            notes.append("对情绪用户讲内部事项 -1.0")
        if f["restate_ask"] and f["complaint"]:
            usefulness -= 1.0
            notes.append("让已表达不满的用户复述问题 -1.0")
        if f["multi_intent"] and not (f["asks_info"] or f["numbered_steps"] >= 2):
            usefulness -= 0.5
            notes.append("多问题未逐项覆盖 -0.5")
    usefulness = _half(_clamp(usefulness))

    discount = 1.0 if (needs and not f["proactive"]) else 0.0
    strict = _half(_clamp(usefulness - discount))
    strict_note = (
        "严格口径：假设系统具备全部查询/代办能力，未执行本可执行的动作要扣分。"
        if discount
        else "严格口径：与能力边界口径一致。"
    )

    if f["proactive"]:
        usefulness_evidence = features.snippet(reply, f["proactive_word"])
    elif f["self_service"]:
        usefulness_evidence = features.snippet(reply, f["self_service_word"])
    else:
        usefulness_evidence = reply[:40] + ("…" if len(reply) > 40 else "")

    specificity = 4.0 if (f["proactive"] or f["numeric_claims"]) else (2.0 if f["self_service"] else 3.0)
    actionability = 4.0 if f["proactive"] else (2.0 if f["self_service"] else 3.0)
    completeness = 4.0 if f["asks_info"] else (2.0 if f["multi_intent"] else 3.0)

    tone = 4.0
    tone_notes = []
    if f["complaint"] or f["fault_report"]:
        if f["apology"]:
            tone += 0.5
            tone_notes.append("有道歉 +0.5")
        else:
            tone -= 1.5
            tone_notes.append("用户有情绪/故障但无道歉 -1.5")
        if f["internal_matter"]:
            tone -= 0.5
            tone_notes.append("讲内部事项 -0.5")
        if f["restate_ask"] and f["complaint"]:
            tone -= 0.5
            tone_notes.append("让情绪用户复述 -0.5")
    elif f["polite"]:
        tone += 0.5
        tone_notes.append("礼貌开场 +0.5")
    tone = _half(_clamp(tone))
    tone_evidence = features.snippet(reply, f["apology_word"]) if f["apology"] else reply[:36]

    fabrication = bool(f["kb_violations"])
    faithfulness = _half(_clamp(5.0 - 3.0 * len(f["kb_violations"])))
    if fabrication:
        faith_reason = "存在与最小知识库冲突或无依据的具体断言。"
    elif f["numeric_claims"]:
        faith_reason = "包含具体数值/时效断言（" + "、".join(f["numeric_claims"]) + "），规则层未发现冲突。"
    else:
        faith_reason = "未包含具体数值/政策断言，未发现编造迹象（规则层无法覆盖全部编造情形）。"
    faith_evidence = "、".join(f["numeric_claims"])[:60] or "（无数值/政策断言）"

    return {
        "case_id": case["id"],
        "source": SOURCE,
        "metrics": {
            "accuracy": {"score": accuracy, "evidence": accuracy_evidence, "reason": accuracy_reason},
            "usefulness": {
                "score": usefulness,
                "evidence": usefulness_evidence,
                "reason": "；".join(notes),
                "sub": {
                    "specificity": _half(specificity),
                    "actionability": _half(actionability),
                    "completeness": _half(completeness),
                },
            },
            "tone": {
                "score": tone,
                "evidence": tone_evidence,
                "reason": "；".join(tone_notes) if tone_notes else "中性场景，语气无明显问题。",
            },
            "faithfulness": {
                "score": faithfulness,
                "evidence": faith_evidence,
                "reason": faith_reason,
                "fabrication": fabrication,
                "unsupported_claims": [v["desc"] for v in f["kb_violations"]],
            },
        },
        "strict_usefulness": strict,
        "strict_note": strict_note,
        "capability_needs": needs,
        "gap_reason": _gap_reason(needs, capabilities),
        "features": f,
    }


def judge_all(cases, capabilities):
    """返回 list：对全部 case 逐条打分。"""
    return [judge_case(case, capabilities) for case in cases]
