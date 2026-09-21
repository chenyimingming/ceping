"""数据加载：读取自动回复、人工参考与"人工信号"标签（用于校验）。"""
import hashlib
import json
from pathlib import Path


def load_json(path):
    """返回 dict/list：读取 UTF-8 JSON 文件。"""
    return json.loads(Path(path).read_text(encoding="utf-8"))


def hash_file(path):
    """返回 str：文件内容的 sha256 前 12 位（报告里标注数据指纹，便于复盘）。"""
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()[:12]


def load_cases(data_dir):
    """返回 list：把 auto_replies.json 与 human_ref.json 合并成 20 条 case（含人工参考与分析）。"""
    data_dir = Path(data_dir)
    replies = load_json(data_dir / "auto_replies.json")
    refs = {r["id"]: r for r in load_json(data_dir / "human_ref.json")}
    cases = []
    for item in replies:
        ref = refs.get(item["id"], {})
        cases.append(
            {
                "id": item["id"],
                "user_question": item["user_question"],
                "auto_reply": item["auto_reply"],
                "human_reference": ref.get("human_reference", ""),
                "annotator_notes": ref.get("annotator_notes", ""),
            }
        )
    return cases


def load_human_signals(data_dir):
    """返回 dict：case_id -> 人工信号标签（由标注分析推导）；文件不存在时返回空 dict。"""
    path = Path(data_dir) / "human_signals.json"
    if not path.exists():
        return {}
    return load_json(path).get("signals", {})


def load_capabilities(path):
    """返回 dict：capability_id -> {"value": true/false/"unknown", "desc": 说明}。"""
    return load_json(path).get("capabilities", {})
