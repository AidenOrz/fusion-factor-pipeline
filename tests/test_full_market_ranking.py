from __future__ import annotations

import numpy as np
import pandas as pd

from bridge.labels import attach_cross_sectional_labels
from bridge.panel import build_fusion_panel
from bridge.universe import attach_universe_masks, universe_summary


def _daily_panel(days: int = 25, stocks: int = 12) -> pd.DataFrame:
    rows = []
    dates = pd.bdate_range("2024-01-02", periods=days)
    for day_index, date in enumerate(dates):
        for stock_index in range(stocks):
            close = 10.0 + stock_index + day_index * (stock_index + 1) * 0.01
            rows.append(
                {
                    "date": date,
                    "code": f"{stock_index + 1:06d}",
                    "open": close - 0.1,
                    "close": close,
                    "volume": 100_000,
                    "amount": 10_000_000.0,
                    "bar_number": 300,
                    "is_st": False,
                    "industry": "A" if stock_index < stocks // 2 else "B",
                    "benchmark_open": 100.0 + day_index,
                    "benchmark_close": 100.5 + day_index,
                }
            )
    return pd.DataFrame(rows)


def _mcap(panel: pd.DataFrame) -> pd.DataFrame:
    rows = []
    dates = sorted(panel["date"].unique())
    for date in dates:
        for stock_index in range(3_900):
            rows.append(
                {
                    "date": date,
                    "code": f"{stock_index + 1:06d}",
                    "float_mcap": float(5_000 - stock_index) * 1_000_000.0,
                    "isST": "0",
                }
            )
    return pd.DataFrame(rows)


def test_universe_masks_use_daily_pit_mcap_ranks(tmp_path):
    panel = _daily_panel(days=25)
    path = tmp_path / "mcap.parquet"
    _mcap(panel).to_parquet(path, index=False)

    result = attach_universe_masks(panel, mcap_path=path)

    assert result["universe_version"].eq("pit_float_mcap_daily_rank").all()
    warmup = result["date"].lt(pd.Timestamp("2024-01-29"))
    assert result.loc[warmup, "universe_b1"].eq(False).all()
    assert result.loc[~warmup, "universe_b1"].all()
    assert result["universe_b2"].eq(False).all()
    assert result["universe_b3"].eq(False).all()
    assert result.loc[warmup, "adv20"].isna().all()

    summary = universe_summary(result)
    assert set(summary["universe"]) == {"b1", "b2", "b3"}


def test_universe_masks_do_not_treat_text_zero_as_st(tmp_path):
    panel = _daily_panel(days=25)
    panel["is_st"] = "0"
    panel.loc[panel.index[0], "is_st"] = "1"
    path = tmp_path / "mcap.parquet"
    _mcap(panel).to_parquet(path, index=False)

    result = attach_universe_masks(panel, mcap_path=path)
    eligible = result.loc[result["date"].ge(pd.Timestamp("2024-01-29"))]
    assert eligible.loc[eligible["is_st"].eq("0"), "universe_b1"].all()


def test_cross_sectional_labels_are_domain_local_and_censored_at_end():
    panel = _daily_panel(days=25)
    panel["float_mcap"] = np.where(panel["code"].astype(int) <= 6, 1e9, 2e9)
    panel["universe_b1"] = True
    panel["universe_b2"] = panel["code"].astype(int).le(6)

    result = attach_cross_sectional_labels(panel, universe_columns=("universe_b1", "universe_b2"))
    first = result.loc[result["date"].eq(result["date"].min())]
    assert first["y_raw_rank20_b1"].notna().all()
    assert first["y_raw_rank20_b1"].between(0, 1).all()
    assert first.loc[first["universe_b2"], "y_raw_rank20_b2"].notna().all()
    assert first.loc[~first["universe_b2"], "y_raw_rank20_b2"].isna().all()
    assert result.loc[result["date"].eq(result["date"].max()), "y_raw_rank20_b1"].isna().all()
    assert result.loc[result["date"].eq(result["date"].max()), "y_neutral_rank20_b1"].isna().all()
    assert result["y_excess20"].notna().sum() == 5 * 12


def test_labels_use_market_dates_and_do_not_skip_a_missing_stock_day():
    panel = _daily_panel(days=25)
    missing_date = panel["date"].sort_values().iloc[1]
    panel = panel.loc[~((panel["code"] == "000001") & panel["date"].eq(missing_date))].copy()
    panel["universe_b1"] = True

    result = attach_cross_sectional_labels(panel, universe_columns=("universe_b1",))
    first = result.loc[(result["code"] == "000001") & result["date"].eq(result["date"].min())]
    assert first["stock_ret20"].isna().all()
    reference = result.loc[(result["code"] == "000002") & result["date"].eq(result["date"].min())]
    assert reference["stock_ret20"].notna().all()


def test_full_market_panel_scope_does_not_read_events(tmp_path):
    panel = _daily_panel(days=45, stocks=12)
    panel["high"] = panel["close"] + 0.2
    panel["low"] = panel["close"] - 0.2
    panel["name"] = "stock"
    panel["amount"] = panel["close"] * panel["volume"] * 100.0
    panel_path = tmp_path / "daily.parquet"
    panel.to_parquet(panel_path, index=False)
    mcap_path = tmp_path / "mcap.parquet"
    _mcap(panel).to_parquet(mcap_path, index=False)
    out_path = tmp_path / "full_market.parquet"

    result = build_fusion_panel(
        [{"id": "close_rank", "expression": "rank(close)", "engine": "builtin"}],
        panel_path=panel_path,
        events_path=tmp_path / "must_not_be_read.parquet",
        mcap_path=mcap_path,
        out_path=out_path,
        scope="full_market",
    )
    output = pd.read_parquet(out_path)
    assert result["scope"] == "full_market"
    assert len(output) == len(panel)
    assert output["panel_scope"].eq("full_market").all()
    assert {"universe_b1", "y_excess20", "y_raw_rank20_b1"} <= set(output.columns)
