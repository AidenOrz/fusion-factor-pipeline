from __future__ import annotations

import numpy as np
import pandas as pd

from bridge.confirmation import _forward_excess_for_interval, bottom_decile_tradeability


def test_forward_excess_interval_uses_requested_market_date_offsets():
    dates = pd.bdate_range("2024-01-02", periods=6)
    rows = []
    for code, scale in (("000001", 1.0), ("000002", 2.0)):
        for index, date in enumerate(dates):
            rows.append(
                {
                    "date": date,
                    "code": code,
                    "open": 10.0 * scale + index,
                    "close": 10.5 * scale + index,
                    "benchmark_open": 100.0 + index,
                    "benchmark_close": 100.5 + index,
                }
            )
    prices = pd.DataFrame(rows)
    result = _forward_excess_for_interval(prices, 2, 3)
    first = result.loc[(result["date"] == dates[0]) & (result["code"] == "000001")].iloc[0]
    expected = (13.5 / 12.0 - 1.0) - (103.5 / 102.0 - 1.0)
    assert np.isclose(first["forward_excess"], expected)
    assert first["exit_date"] == dates[3]


def test_bottom_decile_tradeability_marks_missing_holding_bar():
    dates = pd.bdate_range("2024-01-02", periods=25)
    predictions = pd.DataFrame(
        {
            "date": [dates[0]] * 10,
            "code": [f"{index:06d}" for index in range(10)],
            "prediction": np.arange(10, dtype=float),
            "y_excess20": np.linspace(-0.10, 0.10, 10),
            "adv20": np.full(10, 10_000_000.0),
        }
    )
    status_rows = []
    for date in dates:
        for index in range(10):
            if index == 0 and date == dates[5]:
                continue
            status_rows.append({"date": date, "code": f"{index:06d}", "is_st": False})
    rows, summary = bottom_decile_tradeability(
        predictions,
        pd.DataFrame(status_rows),
        diagnostic_end=dates[20],
    )
    assert len(rows) == 1
    assert bool(rows.iloc[0]["missing_holding_bar"]) is True
    assert bool(rows.iloc[0]["passes_observable_screens"]) is False
    assert summary["all_q1"]["rows"] == 1
    assert summary["combined_observable_screens"]["rows"] == 0
