"""Multi-provider factor expression miner (DeepSeek / OpenAI / Anthropic).

The same factor-mining prompt is used across providers. Keys are read only
from environment or gitignored .env files and are never written to disk.
Every run writes a complete audit trail under
``runs/llm_miner/<timestamp>/``:

* request.json      — provider/model/parameters + prompt (NO key)
* response.json     — raw API response fields (content/usage/served model)
* expressions.json  — validated expression specs
* manifest.json     — run summary + SHA-256 of the other three files

Provider selection:
    --provider deepseek|openai|anthropic|claude   (claude == anthropic)
    --model <model-id>                            (overrides profile/env default)
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd
import requests

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from bridge.paths import require_upstream_root, validate_startup_or_exit

PROJECT_ROOT = Path(__file__).resolve().parents[1]
PROFILES_PATH = PROJECT_ROOT / "config" / "llm_profiles.json"
OUT_PATH = PROJECT_ROOT / "config" / "expressions_llm.json"
RUNS_ROOT = PROJECT_ROOT / "runs" / "llm_miner"


def _read_env_files(paths: list[Path]) -> dict[str, str]:
    env: dict[str, str] = {}
    for path in paths:
        if not path.is_file():
            continue
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                key, _, value = line.partition("=")
                env.setdefault(key.strip(), value.strip())
    return env


def _load_profiles() -> dict[str, dict[str, str]]:
    if PROFILES_PATH.is_file():
        return json.loads(PROFILES_PATH.read_text(encoding="utf-8"))
    return {}


def _prompt(n: int) -> str:
    return f"""你是A股量化因子研究员。请生成 {n} 个日频、做多方向（long-only）的横截面选股因子表达式。

硬性约束：
1. 只能使用 QuantGPT 表达式语法：rank/zscore/ts_mean/ts_std/ts_shift/ts_delta/ts_decay_linear/ts_rank/ts_corr/ts_cov/sign_power/power/where/clip 等。
2. 只允许使用这些列：open, high, low, close, volume, amount, vwap, returns。禁止使用任何基本面列（roe/pe/total_share 等）。
3. 每个表达式必须完整合法、括号平衡；优先连续值表达式，避免 where() 产生离散值。
4. 覆盖不同逻辑：动量、短期反转、低波动、量价相关、成交量异动、VWAP偏离、区间收缩、非线性变换，并允许 2-3 个复合因子。
5. 输出只包含一个 JSON 数组，不要 markdown 代码块，不要解释。数组元素格式：
{{"id":"f001","expression":"rank(close/ts_mean(close,20))","direction":"long","hypothesis":"一句话假设"}}
"""


def _clean_json(text: str) -> str:
    text = text.strip()
    text = re.sub(r"^```(?:json)?\s*", "", text)
    text = re.sub(r"\s*```$", "", text)
    start = text.find("[")
    end = text.rfind("]")
    if start >= 0 and end > start:
        return text[start : end + 1]
    return text


def _extract_last_json_array(text: str) -> list | None:
    """Find the LAST valid JSON array of expression objects in messy text.

    DeepSeek v4 reasoning models can put a valid array at the end of a long
    ``reasoning_content`` field, with fragments of arrays earlier. Trying
    every ``[`` from right to left is the robust extraction strategy.
    """
    decoder = json.JSONDecoder()
    positions = [index for index, char in enumerate(text) if char == "["]
    for start in reversed(positions):
        try:
            value, _ = decoder.raw_decode(text[start:])
        except json.JSONDecodeError:
            continue
        if (
            isinstance(value, list)
            and value
            and all(isinstance(item, dict) for item in value)
            and any("expression" in item for item in value)
        ):
            return value
    return None


def _call_openai_compatible(base_url: str, key: str, model: str, prompt: str, max_tokens: int) -> dict:
    response = requests.post(
        f"{base_url.rstrip('/')}/chat/completions",
        headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
        json={
            "model": model,
            "messages": [{"role": "user", "content": prompt}],
            "temperature": 0.7,
            "max_tokens": max_tokens,
        },
        timeout=180,
    )
    response.raise_for_status()
    payload = response.json()
    return {
        "provider": "openai_compatible",
        "requested_model": model,
        "served_model": payload.get("model"),
        "content": payload["choices"][0]["message"]["content"],
        "usage": payload.get("usage"),
        "raw": payload,
    }


def _call_anthropic(base_url: str, key: str, model: str, prompt: str, max_tokens: int) -> dict:
    response = requests.post(
        f"{base_url.rstrip('/')}/v1/messages",
        headers={
            "x-api-key": key,
            "anthropic-version": "2023-06-01",
            "content-type": "application/json",
        },
        json={
            "model": model,
            "max_tokens": max_tokens,
            "system": "你是一个严谨的A股量化因子表达式生成器。只输出合法 JSON 数组。",
            "messages": [{"role": "user", "content": prompt}],
        },
        timeout=180,
    )
    response.raise_for_status()
    payload = response.json()
    parts = payload.get("content", [])
    content = "\n".join(part.get("text", "") for part in parts if part.get("type") == "text")
    return {
        "provider": "anthropic",
        "requested_model": model,
        "served_model": payload.get("model"),
        "content": content,
        "usage": payload.get("usage"),
        "raw": payload,
    }


def _response_content(call: dict) -> str:
    """Return response text, falling back to OpenAI-compatible reasoning only."""
    content = str(call.get("content") or "").strip()
    if content:
        return content
    raw = call.get("raw")
    if not isinstance(raw, dict):
        return ""
    choices = raw.get("choices")
    if not isinstance(choices, list) or not choices or not isinstance(choices[0], dict):
        return ""
    message = choices[0].get("message")
    if not isinstance(message, dict):
        return ""
    return str(message.get("reasoning_content") or "").strip()


def _build_validation_dummy(n_days: int = 60, n_stocks: int = 20) -> pd.DataFrame:
    """Build deterministic cross-sectional history for expression validation."""
    if n_days < 2 or n_stocks < 2:
        raise ValueError("validation dummy requires at least two days and two stocks")
    dates = pd.bdate_range("2024-01-01", periods=n_days)
    steps = np.arange(n_days, dtype=float)
    frames: list[pd.DataFrame] = []
    for stock_index in range(n_stocks):
        code = f"{100000 + stock_index:06d}.SZ"
        if stock_index == n_stocks - 1:
            close = np.full(n_days, 10.0)
        else:
            close = (
                10.0
                + stock_index * 0.2
                + steps * 0.01
                + 0.15 * np.sin((steps + stock_index) * 0.37)
            )
        open_ = close * (1.0 + 0.002 * np.sin((steps + stock_index) * 0.23))
        high = np.maximum(open_, close) * 1.003
        low = np.minimum(open_, close) * 0.997
        volume = 100_000.0 + stock_index * 5_000.0 + 20_000.0 * (
            1.0 + np.sin((steps + stock_index) * 0.19)
        )
        returns = np.zeros(n_days, dtype=float)
        returns[1:] = close[1:] / close[:-1] - 1.0
        frames.append(
            pd.DataFrame(
                {
                    "trade_date": dates,
                    "stock_code": code,
                    "open": open_,
                    "high": high,
                    "low": low,
                    "close": close,
                    "volume": volume,
                    "amount": volume * close,
                    "vwap": close,
                    "returns": returns,
                }
            )
        )
    return pd.concat(frames, ignore_index=True)


def _evaluate_validation_expression(expression: str, dummy: pd.DataFrame) -> pd.Series:
    quantgpt_root = require_upstream_root("quantgpt")
    if str(quantgpt_root) not in sys.path:
        sys.path.insert(0, str(quantgpt_root))
    from quantgpt.expression_parser import parse_expression

    value = parse_expression(expression)(dummy)
    if isinstance(value, pd.Series):
        series = value.reindex(dummy.index)
    else:
        series = pd.Series(value, index=dummy.index)
    return pd.to_numeric(series, errors="coerce")


def _resolve_provider_config(
    provider: str, model_override: str | None, env: dict[str, str]
) -> dict[str, str]:
    profiles = _load_profiles()
    profile = profiles.get(provider, {})

    if provider in ("deepseek",):
        key = env.get("DEEPSEEK_API_KEY", profile.get("env_key", ""))
        base = env.get("DEEPSEEK_BASE_URL", profile.get("base_url", "https://api.deepseek.com/v1"))
        model = model_override or env.get("DEEPSEEK_MODEL") or profile.get("model") or "deepseek-v4-flash"
    elif provider in ("openai",):
        key = env.get("OPENAI_API_KEY", profile.get("env_key", ""))
        base = env.get("OPENAI_BASE_URL", profile.get("base_url", "https://api.openai.com/v1"))
        model = model_override or env.get("OPENAI_MODEL") or profile.get("model") or "gpt-4.1-mini"
    elif provider in ("anthropic", "claude"):
        key = env.get("ANTHROPIC_API_KEY", profile.get("env_key", ""))
        base = env.get("ANTHROPIC_BASE_URL", profile.get("base_url", "https://api.anthropic.com"))
        model = model_override or env.get("ANTHROPIC_MODEL") or profile.get("model") or "claude-sonnet-4-20250514"
    else:
        raise ValueError(f"Unknown provider: {provider}")

    if not key:
        raise RuntimeError(f"API key not configured for provider {provider}")
    max_tokens = int(profile.get("max_tokens", 12000))
    return {"key": key, "base_url": base, "model": model, "max_tokens": max_tokens}


def validate_expressions(specs: list[dict], dummy: pd.DataFrame | None = None) -> list[dict]:
    dummy = dummy if dummy is not None else _build_validation_dummy()
    clean = []
    for spec in specs:
        if not all(k in spec for k in ("id", "expression", "direction", "hypothesis")):
            continue
        try:
            output = _evaluate_validation_expression(spec["expression"], dummy)
            if output.notna().sum() == 0:
                print(
                    f"[miner] skip invalid {spec.get('id')}: all-NaN on validation dummy",
                    file=sys.stderr,
                )
                continue
            clean.append({**spec, "engine": "quantgpt"})
        except Exception as exc:
            print(f"[miner] skip invalid {spec.get('id')}: {exc}", file=sys.stderr)
    return clean


def deduplicate_semantic(
    specs: list[dict],
    dummy: pd.DataFrame | None = None,
    *,
    threshold: float = 0.95,
    min_overlap: int = 30,
) -> list[dict]:
    """Drop expressions with near-identical dummy outputs without using returns."""
    if not 0.0 < threshold <= 1.0:
        raise ValueError("semantic deduplication threshold must be in (0, 1]")
    dummy = dummy if dummy is not None else _build_validation_dummy()
    kept: list[tuple[dict, pd.Series]] = []
    for spec in specs:
        try:
            output = _evaluate_validation_expression(spec["expression"], dummy)
        except Exception as exc:
            print(f"[miner] skip invalid {spec.get('id')}: {exc}", file=sys.stderr)
            continue
        if output.notna().sum() == 0:
            continue
        duplicate_of: str | None = None
        for other, other_output in kept:
            valid = output.notna() & other_output.notna()
            if int(valid.sum()) < min_overlap:
                continue
            correlation = output.loc[valid].corr(other_output.loc[valid], method="spearman")
            if np.isfinite(correlation) and abs(correlation) >= threshold:
                duplicate_of = str(other["id"])
                break
        if duplicate_of is not None:
            print(
                f"[miner] skip duplicate {spec.get('id')}: semantic match to {duplicate_of}",
                file=sys.stderr,
            )
            continue
        kept.append((spec, output))
    return [spec for spec, _ in kept]


def _sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _write_json(path: Path, payload: dict) -> None:
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def main() -> int:
    parser = argparse.ArgumentParser(description="Multi-provider LLM factor miner")
    parser.add_argument("--count", type=int, default=10)
    parser.add_argument("--out", default=str(OUT_PATH))
    parser.add_argument(
        "--provider",
        default="deepseek",
        choices=["deepseek", "openai", "anthropic", "claude"],
    )
    parser.add_argument("--model", default=None)
    parser.add_argument("--max-tokens", type=int, default=None)
    args = parser.parse_args()

    quantgpt_root = validate_startup_or_exit(("quantgpt",))["quantgpt"]
    env = _read_env_files([PROJECT_ROOT / ".env", quantgpt_root / ".env"])
    provider = "anthropic" if args.provider == "claude" else args.provider
    config = _resolve_provider_config(provider, args.model, env)
    max_tokens = args.max_tokens or int(config["max_tokens"])

    prompt = _prompt(args.count)
    started = datetime.now(timezone.utc)
    if provider in ("deepseek", "openai"):
        call = _call_openai_compatible(config["base_url"], config["key"], config["model"], prompt, max_tokens)
    else:
        call = _call_anthropic(config["base_url"], config["key"], config["model"], prompt, max_tokens)
    finished = datetime.now(timezone.utc)

    content = _response_content(call)
    if not content:
        raise ValueError("LLM returned empty content and empty reasoning_content")
    specs = _extract_last_json_array(content)
    if specs is None:
        try:
            specs = json.loads(_clean_json(content))
        except json.JSONDecodeError as exc:
            raise ValueError(f"LLM response is not a JSON array: {exc}") from exc
    if not isinstance(specs, list):
        raise ValueError("LLM response is not a JSON array")
    validation_dummy = _build_validation_dummy()
    validated = validate_expressions(specs, validation_dummy)
    accepted = deduplicate_semantic(validated, validation_dummy)
    if not accepted:
        raise RuntimeError("No valid expressions returned by LLM")

    stamp = started.strftime("%Y%m%dT%H%M%SZ")
    run_dir = RUNS_ROOT / stamp
    run_dir.mkdir(parents=True, exist_ok=True)

    request_payload = {
        "timestamp": started.isoformat(),
        "provider": provider,
        "requested_model": config["model"],
        "base_url": config["base_url"],
        "count": args.count,
        "max_tokens": max_tokens,
        "prompt": prompt,
        "prompt_sha256": _sha256_text(prompt),
    }
    response_payload = {
        "timestamp": finished.isoformat(),
        "provider": call["provider"],
        "requested_model": call["requested_model"],
        "served_model": call["served_model"],
        "usage": call.get("usage"),
        "content": call["content"],
        "duration_seconds": (finished - started).total_seconds(),
    }
    expressions_payload = {
        "source": f"{provider}_factor_miner",
        "requested_model": config["model"],
        "served_model": call["served_model"],
        "expressions": accepted,
    }

    _write_json(run_dir / "request.json", request_payload)
    _write_json(run_dir / "response.json", response_payload)
    _write_json(run_dir / "expressions.json", expressions_payload)

    manifest = {
        "run_dir": run_dir.name,
        "provider": provider,
        "requested_model": config["model"],
        "served_model": call["served_model"],
        "generated_at": started.isoformat(),
        "duration_seconds": (finished - started).total_seconds(),
        "candidate_count": len(specs),
        "validated_count": len(validated),
        "deduplicated_count": len(accepted),
        "usage": call.get("usage"),
        "sha256": {
            name: hashlib.sha256((run_dir / name).read_bytes()).hexdigest()
            for name in ("request.json", "response.json", "expressions.json")
        },
    }
    _write_json(run_dir / "manifest.json", manifest)

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(
        json.dumps(expressions_payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(f"[miner] wrote {len(accepted)} expressions to {out}")
    print(f"[miner] run record: {run_dir}")
    print(f"[miner] served_model={call['served_model']}, usage={call.get('usage')}")
    for spec in accepted:
        print(f"- {spec['id']}: {spec['expression']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
