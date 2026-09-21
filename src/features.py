"""规则层特征抽取：把一条 case 的文本转成确定性特征快照。

特征分四组：
1) 用户侧：抱怨、故障、操作困难、"建议类反馈"、个案特征、多意图；
2) 回复侧：道歉、礼貌、主动承接、自助导向、追问、编号步骤、内部事项、让用户复述；
3) 知识库比对：数字与单位断言、与最小知识库的冲突检测；
4) 能力关键词：问题涉及系统的哪些能力（与 capabilities.json 的键对应）。
"""
import re

COMPLAINT_WORDS = [
    "太差", "没人理", "等了", "态度", "生气", "气死", "搞半天", "搞不懂", "太复杂",
    "复杂", "麻烦", "糟糕", "投诉", "不满", "离谱", "烦", "又是", "连续",
]
FAULT_WORDS = [
    "坏了", "故障", "没声音", "不工作", "不亮", "用不了", "不能用", "出问题", "有问题",
    "报错", "死机", "异响", "坏的",
]
DIFFICULTY_WORDS = [
    "搞不懂", "搞不明白", "不会", "怎么操作", "不清楚", "太复杂", "复杂", "卡住", "卡在",
    "搞半天", "取不出", "取不到", "打不开", "进不去", "没法", "用不了",
]
FEEDBACK_WORDS = ["建议", "加个功能", "能不能加", "希望", "反馈"]
CASE_SPECIFIC_WORDS = ["我", "这款", "这个", "那款", "那两个", "买的", "买了", "上次", "我的"]
POLITE_WORDS = ["您好", "你好", "感谢", "谢谢", "抱歉", "对不起"]
DIRECT_SOLUTION_WORDS = ["退货退款", "退款", "换新", "换货", "取消", "申请退货", "补偿", "赔偿", "退回"]
MULTI_INTENT_WORDS = ["顺便", "两个问题", "分别", "还有", "两件"]

APOLOGY_PATTERN = re.compile("抱歉|对不起|给您带来不便|让您久等")
PROACTIVE_PATTERN = re.compile(
    "我帮|我来|我为|我替|帮您|为您|请提供.{0,8}(订单号|编号|账号|单号)|请告诉我|方便告诉|"
    "告诉我.{0,8}(订单号|尺码|商品|型号)|我来处理|现在就帮|我马上|我这就"
)
SELF_SERVICE_PATTERN = re.compile(
    "详情页|订单页|物流(追踪)?页|商品页面|购物车|官网|官方APP|官方app|网站|快递公司|航空公司|"
    "客服|自行|自己|耐心等待|关注商品"
)
ASKS_INFO_PATTERN = re.compile("请提供|请问您|请告诉我|方便告诉|请问是哪|告诉我具体|提供.{0,8}(订单号|单号|编号)")
INTERNAL_PATTERN = re.compile("培训|反馈给(产品|品控|技术)|转达给|品控部门|产品团队|内部")
RESTATE_PATTERN = re.compile("遇到了什么问题|有什么问题|遇到了什么|问题是什么")
STEP_PATTERN = re.compile(r"[1-9][\.、)）]|①|②|③|④|⑤")
NUMERIC_UNIT_PATTERN = re.compile(r"\d+(?:\.\d+)?\s*(?:个)?(?:工作日|天|小时|分钟|Wh|wh|mAh|mah|元|折|%)")

KB_RULES = [
    (
        "民航-充电宝",
        re.compile(r"(?<!不)超过\s*100\s*Wh[^。；]{0,20}(可以|可)(随身|带上)|100\s*Wh\s*以上[^。；]{0,20}(可以|可)(随身|带上)"),
        "充电宝额定能量超过 100Wh 不得随身携带（民航局规定）",
    ),
    (
        "退款时效-信用卡",
        re.compile(r"信用卡[^。；]{0,10}1\s*-\s*3\s*个?工作日"),
        "信用卡退款时效与数据集内口径冲突（数据集内为 5-15 个工作日）",
    ),
]

CAPABILITY_KEYWORDS = {
    "order_lookup": ["订单"],
    "logistics_lookup": ["快递", "物流", "包裹", "取件", "派送"],
    "refund_lookup": ["退款", "到账"],
    "product_lookup": ["成分", "材质", "参数", "容量", "充电宝", "面膜", "手机壳", "耳机", "扫地机器人", "包"],
    "coupon_lookup": ["优惠券", "券"],
    "inventory_lookup": ["补货", "到货", "库存"],
    "account_lookup": ["异地登录", "登录记录", "账号"],
    "after_sales_action": ["退货", "换货", "取消", "换尺码", "退掉"],
    "product_comparison": ["哪个好", "对比", "推荐"],
    "video_feature": ["视频", "实物"],
}


def _hits(text, words):
    return [w for w in words if w in text]


def _first_index(text, words):
    positions = [text.find(w) for w in words if w in text]
    return min(positions) if positions else -1


def snippet(text, word, width=16):
    """返回 str：以命中词为中心截取一段上下文，供报告引用（用于证据展示）。"""
    if not word:
        return text[: width * 2]
    index = text.find(word)
    if index < 0:
        return text[: width * 2]
    start = max(0, index - width)
    end = min(len(text), index + len(word) + width)
    prefix = "…" if start > 0 else ""
    suffix = "…" if end < len(text) else ""
    return prefix + text[start:end] + suffix


def extract_features(case):
    """返回 dict：对单条 case（用户问题 + 自动回复）计算全部规则特征。"""
    question = case["user_question"]
    reply = case["auto_reply"]

    complaint_words = _hits(question, COMPLAINT_WORDS)
    fault_words = _hits(question, FAULT_WORDS)
    difficulty_words = _hits(question, DIFFICULTY_WORDS)

    step_matches = list(STEP_PATTERN.finditer(reply))
    numeric_claims = sorted(set(NUMERIC_UNIT_PATTERN.findall(reply)))
    violations = [
        {"rule": name, "desc": desc}
        for name, pattern, desc in KB_RULES
        if pattern.search(reply)
    ]

    apology_match = APOLOGY_PATTERN.search(reply)
    proactive_match = PROACTIVE_PATTERN.search(reply)
    self_service_match = SELF_SERVICE_PATTERN.search(reply)

    solution_pos = _first_index(reply, DIRECT_SOLUTION_WORDS)
    has_steps = len(step_matches) >= 2
    solution_after_steps = bool(
        fault_words and solution_pos >= 0 and step_matches and solution_pos > step_matches[0].start()
    )

    capability_hits = [
        cap for cap, words in CAPABILITY_KEYWORDS.items() if any(w in question for w in words)
    ]

    return {
        "complaint": bool(complaint_words),
        "complaint_words": complaint_words,
        "fault_report": bool(fault_words),
        "fault_words": fault_words,
        "difficulty": bool(difficulty_words),
        "difficulty_words": difficulty_words,
        "feedback": bool(_hits(question, FEEDBACK_WORDS)),
        "case_specific": bool(_hits(question, CASE_SPECIFIC_WORDS)),
        "multi_intent": bool(_hits(question, MULTI_INTENT_WORDS)) or question.count("？") + question.count("?") >= 2,
        "apology": bool(apology_match),
        "apology_word": apology_match.group(0) if apology_match else "",
        "polite": bool(_hits(reply, POLITE_WORDS)),
        "proactive": bool(proactive_match),
        "proactive_word": proactive_match.group(0) if proactive_match else "",
        "self_service": bool(self_service_match),
        "self_service_word": self_service_match.group(0) if self_service_match else "",
        "asks_info": bool(ASKS_INFO_PATTERN.search(reply)),
        "internal_matter": bool(INTERNAL_PATTERN.search(reply)),
        "restate_ask": bool(RESTATE_PATTERN.search(reply)),
        "direct_solution": solution_pos >= 0,
        "solution_after_steps": solution_after_steps,
        "has_steps": has_steps,
        "numbered_steps": len(step_matches),
        "numeric_claims": numeric_claims,
        "kb_violations": violations,
        "capability_hits": capability_hits,
    }
