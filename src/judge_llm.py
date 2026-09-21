"""LLM 评审器：调用 DeepSeek（OpenAI 兼容接口）按同一评分细则打分。

- 盲评：不把 human_ref 的人工参考/标注分析放进提示词；
- JSON 输出模式（response_format=json_object），自动解析与结构校验；
- 结果缓存（cache/llm_cache.json），重复运行不再重复调用；
- 支持两种粒度：case（每案一次调用）与 metric（每指标一次调用）。
"""
import hashlib
import json
import time
import urllib.error
import urllib.request
from pathlib import Path

from . import features, rubric

REQUIRED_METRICS = ("accuracy", "usefulness", "tone", "faithfulness")


def _clamp_score(value):
    number = max(1.0, min(5.0, float(value)))
    return round(number * 2) / 2


def _post_json(url, headers, payload, timeout, opener=None):
    request = urllib.request.Request(
        url,
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers=headers,
        method="POST",
    )
    open_func = opener or urllib.request.urlopen
    with open_func(request, timeout=timeout) as response:
        return json.loads(response.read().decode("utf-8"))


def _parse_json_content(content):
    text = str(content).strip()
    if text.startswith("```"):
        lines = text.splitlines()
        if lines and lines[0].strip().startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].strip().startswith("```"):
            lines = lines[:-1]
        text = "\n".join(lines)
    start = text.find("{")
    end = text.rfind("}")
    if start < 0 or end <= start:
        raise ValueError("模型输出不是 JSON 对象：" + text[:120])
    return json.loads(text[start : end + 1])


def chat(config, messages, opener=None):
    """返回 dict：调用聊天补全接口并把模型输出解析成 JSON 对象（带重试与 JSON 模式降级）。"""
    api_key = str(config.get("api_key") or "").strip()
    if not api_key:
        raise ValueError("config.json 的 api_key 为空：请填入你的 DeepSeek API Key（该文件不会提交到仓库）")
    url = str(config["base_url"]).rstrip("/") + "/chat/completions"
    headers = {"Authorization": "Bearer " + api_key, "Content-Type": "application/json"}
    payload = {
        "model": config["model"],
        "messages": messages,
        "temperature": float(config.get("temperature", 0.0)),
        "stream": False,
        "response_format": {"type": "json_object"},
    }
    timeout = float(config.get("timeout_seconds", 60))
    retries = int(config.get("max_retries", 2))
    last_error = None
    for attempt in range(retries + 1):
        try:
            data = _post_json(url, headers, payload, timeout, opener)
            return _parse_json_content(data["choices"][0]["message"]["content"])
        except urllib.error.HTTPError as exc:
            body = ""
            try:
                body = exc.read().decode("utf-8", "replace")
            except Exception:
                pass
            if exc.code == 400 and "response_format" in body and "response_format" in payload:
                payload.pop("response_format", None)
                try:
                    data = _post_json(url, headers, payload, timeout, opener)
                    return _parse_json_content(data["choices"][0]["message"]["content"])
                except Exception as inner:
                    last_error = inner
            else:
                last_error = RuntimeError("HTTP %s: %s" % (exc.code, body[:300]))
        except Exception as exc:  # 网络/解析错误统一重试
            last_error = exc
        if attempt < retries:
            time.sleep(2 * (attempt + 1))
    raise RuntimeError("调用 DeepSeek 失败：%s" % last_error)


def normalize_result(case_id, raw, source):
    """返回 dict：校验并裁剪 LLM 返回结构（分数限定 1-5；缺失必填指标时报错）。"""
    if not isinstance(raw, dict):
        raise ValueError("模型输出不是 JSON 对象（%s）" % case_id)
    metrics = {}
    for metric in REQUIRED_METRICS:
        block = raw.get(metric)
        if not isinstance(block, dict) or "score" not in block:
            raise ValueError("模型输出缺少指标 %s（%s）" % (metric, case_id))
        normalized = {
            "score": _clamp_score(block["score"]),
            "evidence": str(block.get("evidence", "")),
            "reason": str(block.get("reason", "")),
        }
        if metric == "usefulness":
            sub = block.get("sub") or {}
            normalized["sub"] = {
                key: _clamp_score(sub.get(key, normalized["score"]))
                for key in ("specificity", "actionability", "completeness")
            }
        if metric == "faithfulness":
            normalized["fabrication"] = bool(block.get("fabrication", False))
            normalized["unsupported_claims"] = [
                str(claim) for claim in (block.get("unsupported_claims") or [])
            ]
        metrics[metric] = normalized
    strict = raw.get("strict_usefulness", metrics["usefulness"]["score"])
    return {
        "case_id": case_id,
        "source": source,
        "metrics": metrics,
        "strict_usefulness": _clamp_score(strict),
        "capability_needs": [str(need) for need in (raw.get("capability_needs") or [])],
        "gap_reason": str(raw.get("gap_reason", "")),
    }


def _cache_key(config, messages):
    material = json.dumps(
        {
            "model": config["model"],
            "temperature": config.get("temperature"),
            "messages": messages,
        },
        ensure_ascii=False,
        sort_keys=True,
    )
    return hashlib.sha256(material.encode("utf-8")).hexdigest()


def _chat_cached(config, messages, opener, cache, stats):
    key = _cache_key(config, messages)
    if cache is not None and key in cache:
        if stats is not None:
            stats["hits"] = stats.get("hits", 0) + 1
        return cache[key]
    if stats is not None:
        stats["misses"] = stats.get("misses", 0) + 1
    content = chat(config, messages, opener=opener)
    if cache is not None:
        cache[key] = content
    return content


def judge_case(case, config, capabilities, opener=None, cache=None, granularity=None, stats=None):
    """返回 dict：单条 case 的 LLM 评分（结构与 mock 一致，并附规则层特征供报告解释）。"""
    mode = granularity or config.get("granularity", "case")
    source = "llm:" + str(config.get("model"))
    if mode == "metric":
        merged = {}
        for metric in REQUIRED_METRICS:
            messages = rubric.build_metric_messages(case, capabilities, metric)
            content = _chat_cached(config, messages, opener, cache, stats)
            if metric == "usefulness":
                merged["usefulness"] = content
                merged["strict_usefulness"] = content.get("strict_usefulness", content.get("score"))
                merged["capability_needs"] = content.get("capability_needs", [])
                merged["gap_reason"] = content.get("gap_reason", "")
            else:
                merged[metric] = content
        result = normalize_result(case["id"], merged, source=source)
    else:
        messages = rubric.build_case_messages(case, capabilities)
        content = _chat_cached(config, messages, opener, cache, stats)
        result = normalize_result(case["id"], content, source=source)
    result["features"] = features.extract_features(case)
    result["granularity"] = mode
    return result


def judge_all(cases, config, capabilities, cache_path=None, use_cache=True, opener=None, granularity=None):
    """返回 (results, stats)：逐条评审；缓存文件支持断点续跑、避免重复计费。"""
    cache = {}
    cache_file = Path(cache_path) if cache_path else None
    if use_cache and cache_file and cache_file.exists():
        cache = json.loads(cache_file.read_text(encoding="utf-8"))
    stats = {"hits": 0, "misses": 0}
    results = []
    for case in cases:
        result = judge_case(
            case,
            config,
            capabilities,
            opener=opener,
            cache=cache if use_cache else None,
            granularity=granularity,
            stats=stats,
        )
        results.append(result)
        if use_cache and cache_file:
            cache_file.parent.mkdir(parents=True, exist_ok=True)
            cache_file.write_text(json.dumps(cache, ensure_ascii=False, indent=2), encoding="utf-8")
    return results, stats


def list_models(config, opener=None):
    """返回 list：向服务端查询当前账号可用的模型名。"""
    api_key = str(config.get("api_key") or "").strip()
    if not api_key:
        raise ValueError("config.json 的 api_key 为空：请填入你的 DeepSeek API Key")
    url = str(config["base_url"]).rstrip("/") + "/models"
    request = urllib.request.Request(url, headers={"Authorization": "Bearer " + api_key}, method="GET")
    open_func = opener or urllib.request.urlopen
    with open_func(request, timeout=float(config.get("timeout_seconds", 60))) as response:
        data = json.loads(response.read().decode("utf-8"))
    return sorted(item.get("id", "") for item in data.get("data", []))
