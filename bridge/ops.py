"""Deterministic factor operators used by the bridge layer.

Every operator is fail-fast: missing required columns raise instead of
silently degrading, and ±inf is always sanitized to NaN with an explicit
count so bad factors can never look like "no signal".
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd


class FactorEvaluationError(ValueError):
    """Raised when an expression cannot be evaluated safely."""


@dataclass(frozen=True)
class SanitizeReport:
    rows: int
    n_nan: int
    n_inf: int
    missing_rate: float

    def as_dict(self) -> dict[str, float]:
        return {
            "rows": float(self.rows),
            "n_nan": float(self.n_nan),
            "n_inf": float(self.n_inf),
            "missing_rate": float(self.missing_rate),
        }


def sanitize(series: pd.Series, name: str = "factor") -> tuple[pd.Series, SanitizeReport]:
    values = pd.to_numeric(series, errors="coerce")
    inf_mask = np.isinf(values)
    n_inf = int(inf_mask.sum())
    values = values.mask(inf_mask)
    n_nan = int(values.isna().sum())
    report = SanitizeReport(
        rows=int(len(values)),
        n_nan=n_nan,
        n_inf=n_inf,
        missing_rate=float(n_nan / len(values)) if len(values) else 1.0,
    )
    return values.rename(name), report


def require_column(frame: pd.DataFrame, column: str, expression: str) -> None:
    if column not in frame.columns:
        raise FactorEvaluationError(
            f"expression {expression!r} requires column {column!r} which is missing; "
            "refusing silent degradation"
        )


def ts_shift(frame: pd.DataFrame, column: str, periods: int = 1) -> pd.Series:
    require_column(frame, column, "ts_shift")
    return frame.groupby("code", sort=False)[column].shift(periods)


def ts_delta(frame: pd.DataFrame, column: str, periods: int = 1) -> pd.Series:
    require_column(frame, column, "ts_delta")
    shifted = ts_shift(frame, column, periods)
    return frame[column] - shifted


def ts_mean(frame: pd.DataFrame, column: str, window: int) -> pd.Series:
    require_column(frame, column, "ts_mean")
    return (
        frame.groupby("code", sort=False)[column]
        .rolling(window, min_periods=max(1, window // 2))
        .mean()
        .reset_index(level=0, drop=True)
        .sort_index()
    )


def ts_std(frame: pd.DataFrame, column: str, window: int) -> pd.Series:
    require_column(frame, column, "ts_std")
    return (
        frame.groupby("code", sort=False)[column]
        .rolling(window, min_periods=max(2, window // 2))
        .std()
        .reset_index(level=0, drop=True)
        .sort_index()
    )


def ts_decay_linear(frame: pd.DataFrame, column: str, window: int) -> pd.Series:
    require_column(frame, column, "ts_decay_linear")
    weights = np.arange(1, window + 1, dtype=float)
    weights /= weights.sum()
    parts = [ts_shift(frame, column, period) * weight for period, weight in enumerate(weights)]
    return sum(parts)


def ts_corr(frame: pd.DataFrame, left: str, right: str, window: int) -> pd.Series:
    """Rolling correlation with explicit zero-variance handling.

    Zero-variance windows return NaN (and are counted), never inf, never a
    silently fabricated 0/1 correlation.
    """
    require_column(frame, left, "ts_corr")
    require_column(frame, right, "ts_corr")
    grouped = frame.groupby("code", sort=False)
    out: list[pd.Series] = []
    for _, group in grouped:
        result = group[left].rolling(window, min_periods=2).corr(group[right])
        std_left = group[left].rolling(window, min_periods=2).std(ddof=0)
        std_right = group[right].rolling(window, min_periods=2).std(ddof=0)
        zero_variance = (std_left == 0) | (std_right == 0)
        result = result.mask(zero_variance)
        out.append(result)
    if not out:
        return pd.Series(dtype=float)
    return pd.concat(out).sort_index()


def cross_rank(frame: pd.DataFrame, column: str, *, pct: bool = True) -> pd.Series:
    require_column(frame, column, "cross_rank")
    return frame.groupby("date", sort=False)[column].rank(pct=pct, method="average")


def group_rank(
    frame: pd.DataFrame,
    column: str,
    group_column: str,
    *,
    pct: bool = True,
) -> pd.Series:
    """Cross-sectional rank within (date, group). Never degrades to rank()."""
    require_column(frame, column, "group_rank")
    require_column(frame, group_column, "group_rank")
    ranked = frame.groupby(["date", group_column], sort=False)[column].rank(
        pct=pct, method="average"
    )
    # pandas categorical codes use -1 for missing industries. Those rows do
    # not form an economically meaningful peer group.
    if group_column == "industry_code":
        ranked = ranked.mask(frame[group_column].eq(-1))
    return ranked


def market_cap_neutralize(
    frame: pd.DataFrame,
    factor_column: str,
    *,
    cap_column: str = "market_cap",
    industry_column: str | None = None,
) -> pd.Series:
    require_column(frame, factor_column, "market_cap_neutralize")
    require_column(frame, cap_column, "market_cap_neutralize")
    if industry_column is not None:
        require_column(frame, industry_column, "market_cap_neutralize")
        log_cap = np.log(frame[cap_column].clip(lower=1e-12))
        keys = ["date", industry_column]
    else:
        keys = ["date"]
        log_cap = np.log(frame[cap_column].clip(lower=1e-12))
    work = frame.assign(_factor=frame[factor_column], _log_cap=log_cap)
    demeaned = work.groupby(keys, sort=False)[["_factor", "_log_cap"]].transform(
        lambda x: x - x.mean()
    )
    # Residual of factor on log market cap per (date[, industry]).
    factor = demeaned["_factor"]
    cap = demeaned["_log_cap"]
    slope = (factor * cap).groupby([work[k] for k in keys]).transform("sum") / (
        (cap**2).groupby([work[k] for k in keys]).transform("sum").replace(0, np.nan)
    )
    return factor - slope * cap


def evaluate_builtin(
    frame: pd.DataFrame,
    expression: str,
    *,
    expression_id: str = "expr",
) -> tuple[pd.Series, SanitizeReport]:
    """Evaluate a small deterministic expression subset.

    Supported: ``rank(x)``, ``ts_mean(x, N)``, ``ts_std(x, N)``,
    ``ts_shift(x, N)``, ``ts_delta(x, N)``, ``ts_decay_linear(x, N)``,
    ``group_rank(x, group)``, unary ``-x``, and top-level ``+``, ``-``,
    ``*``, and ``/`` arithmetic.
    Anything else raises instead of silently guessing.
    """
    text = expression.strip()
    if text.startswith("rank(") and text.endswith(")"):
        inner = text[5:-1].strip()
        if inner in frame.columns:
            return sanitize(cross_rank(frame, inner), expression_id)
        inner_series, _ = evaluate_builtin(frame, inner, expression_id=expression_id)
        temp = f"__rank_inner_{abs(hash(expression))}"
        work = frame.assign(**{temp: inner_series})
        return sanitize(cross_rank(work, temp), expression_id)
    if text.startswith("group_rank(") and text.endswith(")"):
        args = _split_args(text[len("group_rank(") : -1])
        if len(args) != 2:
            raise FactorEvaluationError(f"group_rank expects 2 args, got {len(args)}")
        value, group = _resolve_column(frame, args[0], expression), _resolve_column(
            frame, args[1], expression
        )
        return sanitize(group_rank(frame, value, group), expression_id)
    for op_name, func in (
        ("ts_decay_linear", ts_decay_linear),
        ("ts_mean", ts_mean),
        ("ts_std", ts_std),
        ("ts_delta", ts_delta),
        ("ts_shift", ts_shift),
    ):
        prefix = f"{op_name}("
        if _is_top_level_call(text, prefix):
            args = _split_args(text[len(prefix) : -1])
            if op_name == "ts_shift" and len(args) == 1:
                return sanitize(func(frame, _resolve_column(frame, args[0], expression)), expression_id)
            if len(args) != 2:
                raise FactorEvaluationError(f"{op_name} expects 2 args, got {len(args)}")
            window = int(float(args[1]))
            if args[0] in frame.columns:
                return sanitize(func(frame, args[0], window), expression_id)
            series, _ = evaluate_builtin(frame, args[0], expression_id=expression_id)
            temp = f"__ts_arg_{abs(hash(expression))}_{op_name}"
            work = frame.assign(**{temp: series})
            return sanitize(func(work, temp, window), expression_id)
    if _is_top_level_call(text, "ts_corr("):
        args = _split_args(text[len("ts_corr(") : -1])
        if len(args) != 3:
            raise FactorEvaluationError("ts_corr expects 3 args")
        window = int(float(args[2]))
        work = frame

        def _series_or_column(arg: str) -> str:
            nonlocal work
            if arg in frame.columns:
                return arg
            series, _ = evaluate_builtin(frame, arg, expression_id=expression_id)
            temp = f"__corr_arg_{abs(hash((expression, arg)))}"
            work = work.assign(**{temp: series})
            return temp

        left = _series_or_column(args[0])
        right = _series_or_column(args[1])
        return sanitize(ts_corr(work, left, right, window), expression_id)
    if text.startswith("-") and len(text) > 1:
        inner = text[1:].strip()
        series, report = evaluate_builtin(frame, inner, expression_id=expression_id)
        return sanitize(-series, expression_id)
    if text.startswith("(") and _closing_paren_is_final(text):
        inner = text[1:-1].strip()
        series, report = evaluate_builtin(frame, inner, expression_id=expression_id)
        return sanitize(series, expression_id)
    binary = _split_top_level_add_sub(text)
    if binary is not None:
        left_text, op, right_text = binary
        left, _ = evaluate_builtin(frame, left_text.strip(), expression_id=expression_id)
        right, _ = evaluate_builtin(frame, right_text.strip(), expression_id=expression_id)
        value = left + right if op == "+" else left - right
        return sanitize(value, expression_id)
    binary = _split_top_level_mul_div(text)
    if binary is not None:
        left_text, op, right_text = binary
        left, _ = evaluate_builtin(frame, left_text.strip(), expression_id=expression_id)
        right, _ = evaluate_builtin(frame, right_text.strip(), expression_id=expression_id)
        value = left * right if op == "*" else left / right.replace(0, np.nan)
        return sanitize(value, expression_id)
    try:
        constant = float(text)
    except ValueError:
        constant = None
    if constant is not None:
        return sanitize(pd.Series(constant, index=frame.index), expression_id)
    column = _resolve_column(frame, text, expression)
    return sanitize(frame[column], expression_id)


def _resolve_column(frame: pd.DataFrame, text: str, expression: str) -> str:
    column = text.strip()
    if column not in frame.columns:
        raise FactorEvaluationError(
            f"expression {expression!r} references column {column!r} which is missing"
        )
    return column


def _split_top_level_add_sub(text: str) -> tuple[str, str, str] | None:
    depth = 0
    candidate: tuple[int, str] | None = None
    for index, char in enumerate(text):
        if char == "(":
            depth += 1
        elif char == ")":
            depth -= 1
        elif depth == 0 and char in "+-" and index > 0:
            candidate = (index, char)
    if candidate is None:
        return None
    index, op = candidate
    return text[:index], op, text[index + 1 :]


def _split_top_level_mul_div(text: str) -> tuple[str, str, str] | None:
    """Split on the final top-level multiplication or division operator."""
    depth = 0
    candidate: tuple[int, str] | None = None
    for index, char in enumerate(text):
        if char == "(":
            depth += 1
        elif char == ")":
            depth -= 1
        elif depth == 0 and char in "*/":
            candidate = (index, char)
    if candidate is None:
        return None
    index, op = candidate
    return text[:index], op, text[index + 1 :]


def _closing_paren_is_final(text: str) -> bool:
    if not text.startswith("(") or not text.endswith(")"):
        return False
    depth = 0
    for index, char in enumerate(text):
        if char == "(":
            depth += 1
        elif char == ")":
            depth -= 1
            if depth == 0:
                return index == len(text) - 1
    return False


def _is_top_level_call(text: str, prefix: str) -> bool:
    """True when text is exactly one function call starting with prefix."""
    if not text.startswith(prefix) or not text.endswith(")"):
        return False
    depth = 0
    open_pos = len(prefix) - 1
    if text[open_pos] != "(":
        return False
    for index in range(open_pos, len(text)):
        if text[index] == "(":
            depth += 1
        elif text[index] == ")":
            depth -= 1
            if depth == 0:
                return index == len(text) - 1
    return False


def _split_args(inner: str) -> list[str]:
    """Split top-level comma-separated args, respecting parentheses."""
    parts: list[str] = []
    depth = 0
    current: list[str] = []
    for char in inner:
        if char == "(":
            depth += 1
        elif char == ")":
            depth -= 1
        if char == "," and depth == 0:
            parts.append("".join(current).strip())
            current = []
        else:
            current.append(char)
    if current:
        parts.append("".join(current).strip())
    return parts
