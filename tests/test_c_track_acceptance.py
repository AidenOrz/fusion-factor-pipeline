from __future__ import annotations

import json

import numpy as np
import pandas as pd
import pytest

from bridge.c_track_acceptance import (
    _curve_metrics,
    _build_b2_baseline_selection,
    _build_signal_trades,
    build_close_matrix,
    compute_index_benchmarks,
    load_frozen_contract,
    resolve_trade_window,
    select_bottom_exclusion,
    simulate_cohort_portfolio,
)


def _panel(dates: int = 8, codes: int = 4) -> pd.DataFrame:
    rows = []
    calendar = pd.bdate_range("2024-10-21", periods=dates)
    for day, date in enumerate(calendar):
        for code_index in range(codes):
            code = f"{code_index + 1:06d}"
            close = 10.0 + code_index + day * 0.1
            rows.append(
                {
                    "date": date,
                    "code": code,
                    "open": close - 0.05,
                    "close": close,
                    "volume": 100_000.0,
                    "amount": 10_000_000.0,
                    "is_st": False,
                    "universe_b2": True,
                    "y_excess20": (code_index - 1.5) / 100.0,
                    "float_mcap": 1_000_000_000.0,
                    "adv20": 10_000_000.0,
                }
            )
    return pd.DataFrame(rows)


def test_select_bottom_exclusion_is_deterministic_and_uses_ceil():
    panel = _panel(dates=1, codes=10)
    predictions = panel[["date", "code"]].copy()
    predictions["prediction"] = np.arange(10, dtype=float)
    selected, excluded = select_bottom_exclusion(predictions, panel, exclusion_fraction=0.1)
    assert len(excluded) == 1
    assert excluded.iloc[0]["code"] == "000001"
    assert len(selected) == 9

    tied = predictions.copy()
    tied["prediction"] = 1.0
    _, tied_excluded = select_bottom_exclusion(tied, panel, exclusion_fraction=0.1)
    assert tied_excluded.iloc[0]["code"] == "000001"


def test_c_track_runtime_path_excludes_lowest_predictions():
    panel = _panel(dates=1, codes=10)
    predictions = panel[["date", "code"]].copy()
    predictions["prediction"] = np.arange(10, dtype=float)
    selected, excluded = _build_signal_trades(predictions, panel, mode="c_track")
    assert excluded["code"].tolist() == ["000001"]
    assert "000001" not in set(selected["code"])
    assert selected["prediction"].min() == 1.0
    assert "y_excess20" in excluded.columns
    assert "adv20" in selected.columns
    assert selected["cohort_size"].eq(len(selected)).all()


def test_resolve_trade_window_defers_missing_bar_and_uses_last_price():
    panel = _panel(dates=6, codes=1)
    calendar = pd.DatetimeIndex(sorted(panel["date"].unique()))
    # Missing the planned exit bar: the next available close is used.
    planned_exit = calendar[2]
    panel = panel.loc[panel["date"] != planned_exit].copy()
    selected = pd.DataFrame({"date": [calendar[0]], "code": ["000001"], "cohort_size": [1]})
    resolved = resolve_trade_window(selected, panel, market_dates=calendar, horizon=2, evaluation_end=calendar[-1])
    row = resolved.iloc[0]
    assert row["entry_date"] == calendar[1]
    assert row["exit_date"] == calendar[3]
    assert row["exit_status"] == "delayed_missing_bar"

    # No bar after the planned exit: settle at the final available close.
    last_panel = panel.loc[panel["date"] <= calendar[1]].copy()
    last = resolve_trade_window(selected, last_panel, market_dates=calendar, horizon=2, evaluation_end=calendar[-1]).iloc[0]
    assert last["exit_status"] == "last_available_price"
    assert last["exit_date"] == calendar[1]

    # A delayed entry after the planned exit is not executable and must not
    # retain an earlier exit price.
    late_panel = _panel(dates=6, codes=1)
    late_panel = late_panel.loc[late_panel["date"] != calendar[1]].copy()
    late_panel.loc[late_panel["date"] == calendar[2], "open"] = 0.0
    late = resolve_trade_window(selected, late_panel, market_dates=calendar, horizon=2, evaluation_end=calendar[-1]).iloc[0]
    assert late["entry_status"] == "entry_after_exit"
    assert pd.isna(late["exit_date"])
    assert pd.isna(late["exit_close"])


def test_simulation_costs_reduce_equity_and_preserves_unfilled_capital():
    panel = _panel(dates=5, codes=2)
    dates = pd.DatetimeIndex(sorted(panel["date"].unique()))
    trades = pd.DataFrame(
        {
            "signal_date": [dates[0], dates[0]],
            "code": ["000001", "000002"],
            "entry_date": [dates[1], dates[1]],
            "exit_date": [dates[3], dates[3]],
            "entry_open": [9.95, 10.95],
            "exit_close": [10.3, 11.3],
            "cohort_size": [4, 4],  # two unresolved names retain half the cash
        }
    )
    gross = simulate_cohort_portfolio(trades, panel, market_dates=dates, cost_multiplier=0.0, cohort_days=2)
    net = simulate_cohort_portfolio(trades, panel, market_dates=dates, cost_multiplier=1.0, cohort_days=2)
    assert gross["daily"]["equity"].iloc[-1] > net["daily"]["equity"].iloc[-1]
    assert gross["daily"]["cash"].iloc[0] > 0.5
    assert net["mean_daily_two_sided_turnover"] > 0


def test_simulation_reuses_a_close_matrix_without_changing_equity():
    panel = _panel(dates=5, codes=2)
    dates = pd.DatetimeIndex(sorted(panel["date"].unique()))
    trades = pd.DataFrame(
        {
            "signal_date": [dates[0]],
            "code": ["000001"],
            "entry_date": [dates[1]],
            "exit_date": [dates[3]],
            "entry_open": [9.95],
            "exit_close": [10.3],
            "cohort_size": [1],
        }
    )
    uncached = simulate_cohort_portfolio(
        trades, panel, market_dates=dates, cost_multiplier=1.0, cohort_days=2
    )
    cached = simulate_cohort_portfolio(
        trades,
        panel,
        market_dates=dates,
        close_matrix=build_close_matrix(panel),
        cost_multiplier=1.0,
        cohort_days=2,
    )
    pd.testing.assert_frame_equal(uncached["daily"], cached["daily"])


def test_curve_metrics_reports_relative_drawdown():
    dates = pd.bdate_range("2024-10-23", periods=4)
    strategy = pd.DataFrame({"date": dates, "equity": [1.0, 1.1, 0.9, 1.0]})
    benchmark = pd.DataFrame({"date": dates, "equity": [1.0, 1.0, 1.0, 1.0]})
    metrics = _curve_metrics(strategy, benchmark)
    assert metrics["relative_max_drawdown"] == pytest.approx(1.0 - 0.9 / 1.1)
    assert metrics["excess_return"] == pytest.approx(0.0)


def test_index_benchmarks_builds_50_50_mix(tmp_path):
    dates = pd.bdate_range("2024-10-23", periods=4)
    for name, values in (("csi1000", [100, 101, 102, 103]), ("csi2000", [200, 198, 201, 202])):
        pd.DataFrame(
            {"date": dates, "open": values, "close": values}
        ).to_csv(tmp_path / f"{name}.csv", index=False)
    curves = compute_index_benchmarks(
        {"csi1000": tmp_path / "csi1000.csv", "csi2000": tmp_path / "csi2000.csv"},
        start=dates[0],
        end=dates[-1],
    )
    assert {"csi1000", "csi2000", "csi_mix_50_50"}.issubset(curves)
    assert curves["csi_mix_50_50"]["equity"].iloc[0] == pytest.approx(1.0)


def test_b2_baseline_uses_all_panel_members_on_signal_date():
    panel = _panel(dates=2, codes=4)
    signal_dates = panel.loc[panel["date"].eq(panel["date"].min()), "date"].unique()
    baseline = _build_b2_baseline_selection(signal_dates, panel)
    assert len(baseline) == 4
    assert baseline["cohort_size"].eq(4).all()


def test_invalid_frozen_contract_fails_fast(tmp_path):
    frozen = tmp_path / "frozen.json"
    frozen.write_text(json.dumps({"status": "draft"}), encoding="utf-8")
    with pytest.raises(ValueError, match="not marked"):
        load_frozen_contract(frozen)


def test_invalid_inputs_fail_fast(tmp_path):
    malformed = tmp_path / "malformed.csv"
    pd.DataFrame({"date": ["2024-01-01"], "close": [1.0]}).to_csv(malformed, index=False)
    with pytest.raises(ValueError, match="missing columns"):
        compute_index_benchmarks({"csi1000": malformed}, start=pd.Timestamp("2024-01-01"), end=pd.Timestamp("2024-01-02"))


def test_fixed_budget_charges_exactly_one_fee_per_side():
    panel = _panel(dates=3, codes=1)
    panel[["open", "close"]] = 10.0
    dates = pd.DatetimeIndex(panel["date"])
    selected = panel.iloc[:1][["date", "code"]].assign(cohort_size=1)
    trades = resolve_trade_window(selected, panel, horizon=1)
    result = simulate_cohort_portfolio(trades, panel, cohort_days=1)
    expected = (1.0 - result["side_cost"]) / (1.0 + result["side_cost"])
    assert result["daily"]["equity"].iloc[-1] == pytest.approx(expected)
    assert result["daily"]["daily_return"].iloc[0] == pytest.approx(expected - 1.0)
    assert result["trades"]["buy_cash"].iloc[0] == pytest.approx(1.0)
    assert result["total_buy_notional"] == pytest.approx(1.0 / (1.0 + result["side_cost"]))
    assert result["annualized_return"] == pytest.approx(expected ** 252 - 1.0)


def test_curve_metrics_includes_first_day_and_first_day_drawdown():
    dates = pd.bdate_range("2024-10-23", periods=2)
    strategy = pd.DataFrame({"date": dates, "equity": [0.9, 0.9], "daily_return": [-0.1, 0.0]})
    benchmark = pd.DataFrame({"date": dates, "equity": [1.0, 1.0], "daily_return": [0.0, 0.0]})
    metrics = _curve_metrics(strategy, benchmark)
    assert metrics["strategy_return"] == pytest.approx(-0.1)
    assert metrics["relative_max_drawdown"] == pytest.approx(0.1)
    assert metrics["strategy_annualized_return"] == pytest.approx(0.9 ** 126 - 1.0)


def test_period_metrics_include_return_on_first_day_of_subset():
    from bridge.c_track_acceptance import _period_rows

    dates = pd.to_datetime(["2024-12-31", "2025-01-02", "2025-01-03"])
    strategy = pd.DataFrame({"date": dates, "equity": [1.0, 1.1, 1.1], "daily_return": [0.0, 0.1, 0.0]})
    benchmark = pd.DataFrame({"date": dates, "equity": [1.0, 1.0, 1.0], "daily_return": [0.0, 0.0, 0.0]})
    rows = {row["period"]: row for row in _period_rows(strategy, benchmark)}
    assert rows["2025H1"]["excess_return"] == pytest.approx(0.1)


def test_mixed_benchmark_keeps_first_day_open_to_close_return(tmp_path):
    dates = pd.bdate_range("2024-10-23", periods=2)
    paths = {}
    for name, close in (("csi1000", 110.0), ("csi2000", 120.0)):
        paths[name] = tmp_path / f"{name}.csv"
        pd.DataFrame({"date": dates, "open": [100.0, close], "close": [close, close]}).to_csv(paths[name], index=False)
    mixed = compute_index_benchmarks(paths, start=dates[0], end=dates[-1])["csi_mix_50_50"]
    assert mixed["equity"].iloc[0] == pytest.approx(1.15)
    assert mixed["daily_return"].iloc[0] == pytest.approx(0.15)


def test_delayed_entry_past_plan_is_rejected_even_when_exit_also_delays():
    panel = _panel(dates=5, codes=1)
    dates = pd.DatetimeIndex(panel["date"])
    selected = panel.iloc[:1][["date", "code"]]
    panel = panel.loc[~panel["date"].isin(dates[1:3])]
    row = resolve_trade_window(selected, panel, market_dates=dates, horizon=2).iloc[0]
    assert row["entry_status"] == "entry_after_exit"
    assert pd.isna(row["exit_date"])


def test_external_calendar_can_exclude_extra_price_rows():
    panel = _panel(dates=5, codes=1)
    dates = pd.DatetimeIndex(panel["date"])
    selected = panel.iloc[:1][["date", "code"]]
    row = resolve_trade_window(selected, panel, market_dates=dates[:4], horizon=2).iloc[0]
    assert row["exit_date"] == dates[2]


def test_live_bottom_exclusion_does_not_require_forward_labels():
    panel = _panel(dates=1, codes=10)[["date", "code", "universe_b2"]]
    predictions = panel[["date", "code"]].assign(prediction=np.arange(10))
    selected, excluded = select_bottom_exclusion(predictions, panel)
    assert len(selected) == 9
    assert excluded["code"].tolist() == ["000001"]


def test_unresolved_names_keep_their_capital_without_cohort_size():
    panel = _panel(dates=4, codes=2)
    panel[["open", "close"]] = 10.0
    dates = pd.DatetimeIndex(sorted(panel["date"].unique()))
    trades = pd.DataFrame({
        "signal_date": [dates[0], dates[0]], "code": ["000001", "000002"],
        "entry_date": [dates[1], pd.NaT], "exit_date": [dates[2], pd.NaT],
        "entry_open": [10.0, np.nan], "exit_close": [10.0, np.nan],
    })
    result = simulate_cohort_portfolio(trades, panel, cohort_days=1, cost_multiplier=0.0)
    assert result["daily"]["cash"].iloc[0] == pytest.approx(0.5)


def test_opportunity_indices_use_the_same_cohorts_and_costs(tmp_path):
    dates = pd.bdate_range("2024-10-21", periods=22)
    paths = {}
    for name in ("csi1000", "csi2000"):
        paths[name] = tmp_path / f"{name}.csv"
        pd.DataFrame({"date": dates, "open": 100.0, "close": 100.0}).to_csv(paths[name], index=False)
    curves = compute_index_benchmarks(
        paths, start=dates[1], end=dates[20], signal_dates=dates[:1], market_dates=dates,
    )
    expected = 0.95 + 0.05 * (1.0 - 0.002) / (1.0 + 0.002)
    for name in ("csi1000", "csi2000", "csi_mix_50_50"):
        assert curves[name]["equity"].iloc[-1] == pytest.approx(expected)
    assert curves["csi1000"]["cash"].iloc[0] == pytest.approx(0.95)
    assert curves["csi1000"]["active_positions"].iloc[-1] == 0
