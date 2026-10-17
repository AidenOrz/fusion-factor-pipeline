# -*- coding: utf-8 -*-
"""Shenwan 2021 level-1 industry code to name, for readable disclosure.

The SW2021 PIT parquet stores sw_l1 as the official numeric code (11, 22, ...),
which is the correct key but unreadable in a report. This module keeps the
31-code mapping in version control so exposure tables are legible without
depending on a gitignored artifact produced by an earlier session.

Verification: applying this map to the forward excluded-set shares reproduces the
independently computed table in docs/INDUSTRY_PIT_2026-10-18.md section 5 (for
example code 22 -> 基础化工 at 10.35%, code 27 -> 电子 at 8.46%). If those two
ever disagree, the map or the taxonomy has drifted and both must be rechecked.

Boundary: this is the SW2021 taxonomy. It must not be applied to the frozen
panel industry column, which uses different short names from a different vendor
and is a static snapshot.
"""
from __future__ import annotations

SW2021_LEVEL1_NAMES: dict[str, str] = {
    "11": "农林牧渔",
    "22": "基础化工",
    "23": "钢铁",
    "24": "有色金属",
    "27": "电子",
    "28": "汽车",
    "33": "家用电器",
    "34": "食品饮料",
    "35": "纺织服饰",
    "36": "轻工制造",
    "37": "医药生物",
    "41": "公用事业",
    "42": "交通运输",
    "43": "房地产",
    "45": "商贸零售",
    "46": "社会服务",
    "48": "银行",
    "49": "非银金融",
    "51": "综合",
    "61": "建筑材料",
    "62": "建筑装饰",
    "63": "电力设备",
    "64": "机械设备",
    "65": "国防军工",
    "71": "计算机",
    "72": "传媒",
    "73": "通信",
    "74": "煤炭",
    "75": "石油石化",
    "76": "环保",
    "77": "美容护理",
}


def sw2021_level1_name(code: object) -> str:
    """Return the readable name for a SW2021 L1 code, or the code if unknown."""

    key = str(code).strip()
    if key.endswith(".0"):
        key = key[:-2]
    return SW2021_LEVEL1_NAMES.get(key, key)


def sw2021_level1_names() -> list[str]:
    return sorted(set(SW2021_LEVEL1_NAMES.values()))
