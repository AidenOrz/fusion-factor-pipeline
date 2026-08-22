# -*- coding: utf-8 -*-
"""S1 #2 前置：baostock 拉取主板逐日 close/volume/turn/isST → 反推流通市值。

用法:
  python _s1_mc_fetch.py --sample 50          # 小样本验证
  python _s1_mc_fetch.py --full               # 全量（3178 只，后台跑）
  python _s1_mc_fetch.py --date-only 2024-06-28  # 只拉指定日窗口（调试）

输出: data/s1_mc_panel.parquet (date, code, close, volume, turn, isST, float_mcap)
"""
import argparse
import time
import pandas as pd
import numpy as np
from pathlib import Path

from bridge.paths import configured_upstream_path, upstream_path, validate_startup_or_exit

PANEL = configured_upstream_path("v6", "artifacts", "panel", "panel.parquet")
OUT = Path(__file__).resolve().parent / "data" / "s1_mc_panel.parquet"

START, END = "2023-06-01", "2025-12-31"
FIELDS = "date,close,volume,turn,isST"

# 调样时点（中证 6/12 月调样，取 6 月末/12 月末最近交易日）
REBAL_DATES = ["2023-06-30", "2023-12-29", "2024-06-28", "2024-12-31", "2025-06-30", "2025-12-31"]


def bs_code(code: str) -> str:
    return ("sh." if code.startswith("6") else "sz.") + code


def fetch_one(bs, code: str) -> list[list[str]]:
    rs = bs.query_history_k_data_plus(
        bs_code(code), FIELDS,
        start_date=START, end_date=END,
        frequency="d", adjustflag="3",
    )
    rows = []
    while rs.error_code == "0" and rs.next():
        rows.append(rs.get_row_data())
    if rs.error_code != "0":
        print(f"  {code} ERR {rs.error_code} {rs.error_msg}")
    return rows


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--sample", type=int, default=0)
    ap.add_argument("--full", action="store_true")
    ap.add_argument("--date-only", type=str, default=None)
    ap.add_argument("--batch-size", type=int, default=300, help="每批拉取数量")
    ap.add_argument("--resume", action="store_true", help="追加模式：跳过已存在代码")
    ap.add_argument("--market-all", action="store_true", help="用 query_all_stock 拉全市场（含创业板/科创）")
    ap.add_argument("--out", type=str, default=str(OUT), help="输出 parquet 路径")
    args = ap.parse_args()

    validate_startup_or_exit(("v6",))
    panel_path = PANEL or upstream_path("v6", "artifacts", "panel", "panel.parquet")
    import baostock as bs
    lg = bs.login()
    if lg.error_code != "0":
        print("login failed:", lg.error_msg)
        return
    try:
        if args.market_all:
            rs = bs.query_all_stock(day="2025-12-31")
            codes = []
            while rs.error_code == "0" and rs.next():
                r = rs.get_row_data()
                mkt, code = r[0].split(".")[0], r[0].split(".")[1].zfill(6)
                # 只保留沪深股票，剔除指数（sh.000xxx 上证指数/sz.399xxx 深证指数等）
                if mkt == "sh" and code.startswith(("600", "601", "603", "605", "688", "689")):
                    codes.append(code)
                elif mkt == "sz" and code.startswith(("000", "001", "002", "003", "300")):
                    codes.append(code)
            print(f"全市场列表(沪深股票): {len(codes)} 只 (query_all_stock)")
        else:
            codes = pd.read_parquet(panel_path, columns=["code"])["code"].astype(str).str.zfill(6).unique()
            codes = list(codes)
        done = set()
        OUTX = Path(args.out)
        if args.resume and OUTX.exists():
            old = pd.read_parquet(OUTX, columns=["code"])
            done = set(old["code"].astype(str))
            codes = [c for c in codes if c not in done]
            print(f"续传: 已有 {len(done)} 只, 剩余 {len(codes)} 只")
        if args.sample:
            rng = np.random.RandomState(42)
            codes = rng.choice(codes, size=min(args.sample, len(codes)), replace=False).tolist()
        codes = codes[: args.batch_size] if args.batch_size else codes
        print(f"本批拉取: {len(codes)} 只")
        if codes:
            import collections
            print("本批前缀:", dict(collections.Counter(c[:2] for c in codes)))

        all_rows = []
        t0 = time.time()
        session = bs
        for i, code in enumerate(codes):
            # baostock 登录会话约 6-7 分钟过期，每 200 只重新登录
            if i % 200 == 0 and i > 0:
                session.logout()
                lg = bs.login()
                if lg.error_code != "0":
                    print(f"  重新登录失败: {lg.error_msg}"); break
                print(f"  [{i}] 已重新登录")
            rows = fetch_one(bs, code)
            for r in rows:
                all_rows.append([code, *r])
            if (i + 1) % 50 == 0:
                el = time.time() - t0
                print(f"  {i+1}/{len(codes)} 完成, 耗时 {el:.0f}s")
        print(f"本批完成: {len(all_rows)} 行, 耗时 {time.time()-t0:.0f}s")

        df = pd.DataFrame(all_rows, columns=["code", "date", "close", "volume", "turn", "isST"])
        df["date"] = pd.to_datetime(df["date"])
        for c in ["close", "volume", "turn"]:
            df[c] = pd.to_numeric(df[c], errors="coerce")
        # 反推流通市值（元）：close × volume(股) / (turn%/100)
        df["float_mcap"] = df["close"] * df["volume"] / (df["turn"] / 100.0)
        df["float_mcap_亿"] = df["float_mcap"] / 1e8
        df = df.dropna(subset=["float_mcap"])

        # 追加合并（先写临时文件再原子替换，避免写坏）
        if OUTX.exists():
            old = pd.read_parquet(OUTX)
            df = pd.concat([old, df], ignore_index=True).drop_duplicates(["code", "date"], keep="last")
        tmp = OUTX.with_suffix(".parquet.tmp")
        df.to_parquet(tmp, index=False)
        tmp.replace(OUTX)
        print(f"已保存: {OUTX} (累计 {len(df)} 行, {df['date'].nunique()} 天, {df['code'].nunique()} 只)")
        print("市值量级校验(亿):", round(float(df["float_mcap_亿"].median()), 0))
    finally:
        bs.logout()


if __name__ == "__main__":
    main()
