"""配置加载：LLM 模式所需的 base_url / model / api_key 等，从项目根的 config.json 读取。"""
import json
from pathlib import Path

DEFAULT_CONFIG = {
    "base_url": "https://api.deepseek.com",
    "model": "deepseek-flash",
    "api_key": "",
    "temperature": 0.0,
    "timeout_seconds": 60,
    "max_retries": 2,
    "granularity": "case",
}


def load_config(path):
    """返回 dict：读取 config.json 并与默认值合并；文件不存在时提示从模板复制。"""
    file_path = Path(path)
    if not file_path.exists():
        raise FileNotFoundError(
            "配置文件不存在：%s（请先从 config.example.json 复制为 config.json 并填入 API Key）" % file_path
        )
    data = json.loads(file_path.read_text(encoding="utf-8"))
    config = dict(DEFAULT_CONFIG)
    config.update(data)
    return config


def require_api_key(config):
    """返回 str：校验 api_key 非空，否则抛出带操作提示的错误。"""
    key = str(config.get("api_key") or "").strip()
    if not key:
        raise ValueError("config.json 的 api_key 为空：请填入你的 DeepSeek API Key（该文件不会提交到仓库）")
    return key
