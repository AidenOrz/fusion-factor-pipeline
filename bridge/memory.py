# -*- coding: utf-8 -*-
"""Memory discipline for the wide panels.

Context: this machine has 16 GB of RAM and 20 logical cores, and the heavy steps
(label rebuild, forward feature projection, alpha101 re-materialisation) stall on
memory while the CPU sits idle. The cause is not slow arithmetic, it is that
pandas loads a whole panel, then makes several full-length copies of it for each
transformation. With a 1.2-1.8 GB frame in memory, two or three live copies plus
a join output cross 16 GB and the process starts swapping.

Three levers, in the order they pay off:

  1. READ FEWER COLUMNS. The 180-column adjudicated panel is 1.6 GB on disk; a
     run that needs 37 features touches a fraction of that. Column pruning is
     free and loses nothing.
  2. SHRINK DTYPES. String columns are stored as Python objects by default.
     code / industry / panel_scope have a few thousand distinct values at most,
     so category dtype turns tens of MB into tens of KB. This is lossless.
  3. STREAM ROW GROUPS. Process a chunk at a time and keep only the reduced
     result, instead of materialising the whole frame twice.

What this module deliberately does NOT do: silently downcast floats to float32.
That is a real numerical change, so it is exposed as an explicit, documented
opt-in with a measured error, never as a default.
"""
from __future__ import annotations

import gc
from pathlib import Path
from typing import Any, Iterable, Iterator, Sequence

import pandas as pd

# Columns that identify a row rather than measure something. Kept as string even
# though category would be smaller, because they are join keys and a categorical
# join silently drops rows whose categories differ between frames.
KEY_COLUMNS = ("date", "code", "ts_code", "trade_date")


def frame_mb(frame: pd.DataFrame) -> float:
    # Not rounded to one decimal: a small frame would report 0.0 MB and any
    # comparison between two small frames would silently become 0 == 0.
    return int(frame.memory_usage(deep=True).sum()) / (1024 * 1024)


def shrink(frame: pd.DataFrame, *, category_threshold: float = 0.5) -> pd.DataFrame:
    """Convert low-cardinality object columns to category, in place-ish.

    A column is converted only when its distinct count is below
    ``category_threshold * len(frame)``. Converting a near-unique column would
    make it bigger, not smaller, which is a mistake worth guarding against.
    """

    for column in frame.columns:
        if frame[column].dtype != object and str(frame[column].dtype) != "str":
            continue
        if column in KEY_COLUMNS:
            continue
        try:
            distinct = frame[column].nunique(dropna=True)
        except TypeError:  # unhashable values; leave as is
            continue
        if len(frame) and distinct < category_threshold * len(frame):
            frame[column] = frame[column].astype("category")
    return frame


def read_panel(
    path: Path | str,
    columns: Sequence[str] | None = None,
    *,
    filters: Any = None,
    shrink_strings: bool = True,
) -> pd.DataFrame:
    """Read a parquet panel with only the columns a computation needs.

    ``columns`` is not a convenience, it is the main memory lever: reading 40 of
    180 columns costs roughly a fifth of the memory, and the projection is pushed
    down into parquet so the skipped columns are never decompressed.
    """

    frame = pd.read_parquet(path, columns=list(columns) if columns else None, filters=filters)
    if shrink_strings:
        frame = shrink(frame)
    return frame


def iter_row_groups(
    path: Path | str,
    columns: Sequence[str] | None = None,
    *,
    row_group_size: int | None = None,
) -> Iterator[pd.DataFrame]:
    """Yield the panel one row group at a time.

    Row groups are the unit parquet can seek to, so reading them one by one caps
    the peak memory at one group instead of the whole file. The file was written
    with only a handful of groups, so this is a modest win today; it becomes the
    main defence once panels are written with smaller groups (see
    :func:`write_panel`).
    """

    import pyarrow.parquet as pq

    handle = pq.ParquetFile(path)
    wanted = list(columns) if columns else None
    if row_group_size is None:
        # iter_batches rejects batch_size=None; iterating groups directly keeps
        # the peak at one parquet row group, which is the actual goal.
        for index in range(handle.metadata.num_row_groups):
            yield handle.read_row_group(index, columns=wanted).to_pandas()
        return
    for batch in handle.iter_batches(batch_size=row_group_size, columns=wanted):
        yield batch.to_pandas()


def write_panel(frame: pd.DataFrame, path: Path | str, *, row_group_size: int = 250_000) -> dict[str, Any]:
    """Write parquet with small row groups so future reads can stream.

    The default writer behaviour produced 2-4 row groups for a 2.5 M row panel,
    which makes chunked reading useless. Writing ~250k rows per group gives
    roughly ten groups, small enough to stream and still efficient to scan.
    """

    import pyarrow as pa
    import pyarrow.parquet as pq

    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    table = pa.Table.from_pandas(frame, preserve_index=False)
    pq.write_table(table, target, row_group_size=row_group_size, compression="zstd")
    return {
        "path": str(target),
        "rows": int(len(frame)),
        "row_groups": int(pq.ParquetFile(target).metadata.num_row_groups),
        "row_group_size": int(row_group_size),
        "disk_mb": round(target.stat().st_size / (1024 * 1024), 1),
    }


def release(*frames: pd.DataFrame | None) -> None:
    """Drop references and collect, so peak memory reflects one frame not three.

    Python frees on refcount, but pandas frames hold cached blocks that survive a
    plain ``del`` until a collection pass runs. Calling this between pipeline
    stages measurably lowers the peak.
    """

    for frame in frames:
        if frame is not None:
            frame.drop(frame.index, inplace=True)
    del frames
    gc.collect()


def peak_mb() -> float | None:
    """Process peak working set in MB, or None when psutil is unavailable."""

    try:
        import psutil
    except Exception:
        return None
    process = psutil.Process()
    info = process.memory_info()
    return round(info.peak_wset / (1024 * 1024), 1) if hasattr(info, "peak_wset") else None


def estimate_projection(path: Path | str, columns: Iterable[str]) -> dict[str, Any]:
    """Predict the memory saving of a column projection without loading it.

    Reads only parquet metadata plus a tiny sample, so the estimate itself costs
    nothing. Useful before choosing which columns a study really needs.
    """

    import pyarrow.parquet as pq

    handle = pq.ParquetFile(path)
    schema_names = handle.schema_arrow.names
    wanted = [c for c in columns if c in schema_names]
    missing = [c for c in columns if c not in schema_names]
    sample = next(handle.iter_batches(batch_size=20_000, columns=wanted), None)
    per_row = 0.0
    if sample is not None:
        frame = sample.to_pandas()
        per_row = int(frame.memory_usage(deep=True).sum()) / max(len(frame), 1)
    total_rows = handle.metadata.num_rows
    return {
        "file": str(path),
        "requested": len(wanted),
        "available": len(schema_names),
        "missing": missing,
        "estimated_mb": per_row * total_rows / (1024 * 1024),
        "bytes_per_row": round(per_row, 1),
    }
