# ContextPM - Contextualized Process Mining
# Copyright (C) 2026 Mohsen Shirali and Zahra Ahmadi (LIRIS, KU Leuven)
#
# This program is free software: you can redistribute it and/or modify it
# under the terms of the GNU General Public License version 3 as published by
# the Free Software Foundation. It is distributed WITHOUT ANY WARRANTY; see the
# LICENSE file or <https://www.gnu.org/licenses/gpl-3.0.html> for details.
from __future__ import annotations


from pathlib import Path
from typing import Any, Dict, Hashable, Iterable, Mapping, Optional

import numpy as np
import pandas as pd


def load_table(file_path: str | Path, **kwargs: Any) -> pd.DataFrame:
    """
    Load a tabular file into a DataFrame based on its extension.

    Supported formats:
        - .csv / .txt
        - .xlsx / .xls
        - .parquet
    """
    file_path = Path(file_path)
    suffix = file_path.suffix.lower()

    if suffix in {".csv", ".txt"}:
        return pd.read_csv(file_path, **kwargs)
    if suffix in {".xlsx", ".xls"}:
        return pd.read_excel(file_path, **kwargs)
    if suffix == ".parquet":
        return pd.read_parquet(file_path, **kwargs)

    raise ValueError(f"Unsupported file type: {suffix}")


def standardize_activity_names(
    df: pd.DataFrame,
    activity_column: str = "Activity",
    delimiter: str = " - ",
    keep_part: str = "first",
) -> pd.DataFrame:
    """
    Standardize activity names by splitting on a delimiter.

    Example:
        'Sleeping - Night - Good' -> 'Sleeping' when keep_part='first'
    """
    if activity_column not in df.columns:
        raise KeyError(f"Column '{activity_column}' was not found in the DataFrame.")

    if keep_part not in {"first", "last"}:
        raise ValueError("keep_part must be either 'first' or 'last'.")

    output = df.copy()
    parts = output[activity_column].astype(str).str.split(delimiter)

    if keep_part == "first":
        output[activity_column] = parts.str[0]
    else:
        output[activity_column] = parts.str[-1]

    return output


def apply_filters(
    df: pd.DataFrame,
    include_filters: Optional[Mapping[str, Iterable[Any]]] = None,
    exclude_filters: Optional[Mapping[str, Iterable[Any]]] = None,
) -> pd.DataFrame:
    """
    Apply reusable include/exclude filters to a DataFrame.

    Parameters
    ----------
    include_filters:
        {'column_name': ['allowed_value_1', 'allowed_value_2']}
    exclude_filters:
        {'column_name': ['blocked_value_1', 'blocked_value_2']}
    """
    filtered = df.copy()

    if include_filters:
        for column, values in include_filters.items():
            if column not in filtered.columns:
                raise KeyError(f"Column '{column}' was not found in the DataFrame.")
            value_set = set(values)
            filtered = filtered[filtered[column].isin(value_set)]

    if exclude_filters:
        for column, values in exclude_filters.items():
            if column not in filtered.columns:
                raise KeyError(f"Column '{column}' was not found in the DataFrame.")
            value_set = set(values)
            filtered = filtered[~filtered[column].isin(value_set)]

    return filtered.copy()


def split_dataframe_by_column(
    df: pd.DataFrame,
    column_name: str,
    values: Optional[Iterable[Any]] = None,
    dropna: bool = True,
) -> Dict[Hashable, pd.DataFrame]:
    """
    Split a DataFrame into sub-DataFrames based on the values of one column.

    Parameters
    ----------
    df:
        Input DataFrame.
    column_name:
        Name of the column used for splitting.
    values:
        Optional list of specific values to keep. If None, all unique values
        in the column are used.
    dropna:
        If True, missing values are ignored.

    Returns
    -------
    dict
        Example:
            {'2': df_for_value_2, '3': df_for_value_3}
        or with numeric keys:
            {2: df_for_value_2, 3: df_for_value_3}
    """
    if column_name not in df.columns:
        raise KeyError(f"Column '{column_name}' was not found in the DataFrame.")

    series = df[column_name]

    if values is None:
        values = series.dropna().unique() if dropna else series.unique()

    split_frames: Dict[Hashable, pd.DataFrame] = {}
    for value in values:
        if pd.isna(value):
            if dropna:
                continue
            subset = df[series.isna()].copy()
        else:
            subset = df[series == value].copy()

        split_frames[value] = subset.reset_index(drop=True)

    return split_frames


def add_duration_seconds(
    df: pd.DataFrame,
    duration_col: Optional[str] = None,
    start_col: Optional[str] = None,
    end_col: Optional[str] = None,
    output_col: str = "duration_seconds",
) -> pd.DataFrame:
    """
    Add a numeric duration column in seconds.

    One of the following must be provided:
        - duration_col (expects 'hh:mm:ss' format)
        - start_col and end_col
    """
    output = df.copy()

    if duration_col:
        if duration_col not in output.columns:
            raise KeyError(f"Column '{duration_col}' was not found in the DataFrame.")
        
        # Treat the duration as a string, split by ':', and multiply to get seconds
        parts = output[duration_col].astype(str).str.split(':', expand=True)
        
        # Ensure we actually got 3 parts (hh, mm, ss) out of the split
        if parts.shape[1] == 3:
            hours = pd.to_numeric(parts[0], errors='coerce').fillna(0)
            minutes = pd.to_numeric(parts[1], errors='coerce').fillna(0)
            seconds = pd.to_numeric(parts[2], errors='coerce').fillna(0)
            
            output[output_col] = (hours * 3600) + (minutes * 60) + seconds
        else:
            # Fallback if the column turns out to not actually be strictly hh:mm:ss
            output[output_col] = pd.to_numeric(output[duration_col], errors="coerce")
            
        return output

    if start_col and end_col:
        if start_col not in output.columns or end_col not in output.columns:
            missing = [c for c in [start_col, end_col] if c not in output.columns]
            raise KeyError(f"Missing required columns: {missing}")
        start_ts = pd.to_datetime(output[start_col], errors="coerce")
        end_ts = pd.to_datetime(output[end_col], errors="coerce")
        output[output_col] = (end_ts - start_ts).dt.total_seconds()
        return output

    raise ValueError("Provide either duration_col or both start_col and end_col.")


def add_day_column(
    df: pd.DataFrame,
    day_col: Optional[str] = None,
    timestamp_col: Optional[str] = None,
    output_col: str = "day",
) -> pd.DataFrame:
    """
    Ensure a day-level column exists.
    """
    output = df.copy()

    if day_col and day_col in output.columns:
        output[output_col] = pd.to_datetime(output[day_col], errors="coerce").dt.date
        return output

    if timestamp_col and timestamp_col in output.columns:
        output[output_col] = pd.to_datetime(output[timestamp_col], errors="coerce").dt.date
        return output

    raise ValueError("Provide a valid day_col or timestamp_col.")


def compute_activity_summary(
    df: pd.DataFrame,
    activity_col: str,
    duration_col: str = "duration_seconds",
    sort_by: str = "total_duration_seconds",
) -> pd.DataFrame:
    """
    Compute a simple activity summary table.
    """
    required = {activity_col, duration_col}
    missing = required - set(df.columns)
    if missing:
        raise KeyError(f"Missing required columns: {sorted(missing)}")

    summary = (
        df.groupby(activity_col, dropna=False)[duration_col]
        .agg(
            event_count="count",
            total_duration_seconds="sum",
            average_duration_seconds="mean",
            median_duration_seconds="median",
        )
        .sort_values(sort_by, ascending=False)
        .reset_index()
    )

    return summary


def compute_average_duration_per_day(
    df: pd.DataFrame,
    activity_col: str,
    day_col: str = "day",
    duration_col: str = "duration_seconds",
) -> pd.Series:
    """
    Compute the average duration spent on each activity per day.

    This is often a useful baseline for node sizes in the process graph.
    """
    required = {activity_col, day_col, duration_col}
    missing = required - set(df.columns)
    if missing:
        raise KeyError(f"Missing required columns: {sorted(missing)}")

    daily = (
        df.groupby([day_col, activity_col], dropna=False)[duration_col]
        .sum()
        .reset_index()
    )

    averages = (
        daily.groupby(activity_col, dropna=False)[duration_col]
        .mean()
        .sort_values(ascending=False)
    )

    return averages

def compute_average_event_count_per_day(
    df: pd.DataFrame,
    activity_col: str,
    day_col: str = "day",
) -> pd.Series:
    """
    Compute the average number of occurrences of each activity per day.
    """
    required = {activity_col, day_col}
    missing = required - set(df.columns)
    if missing:
        raise KeyError(f"Missing required columns: {sorted(missing)}")

    daily_counts = (
        df.groupby([day_col, activity_col], dropna=False)
        .size()
        .rename("count")
        .reset_index()
    )

    averages = (
        daily_counts.groupby(activity_col, dropna=False)["count"]
        .mean()
        .sort_values(ascending=False)
    )

    return averages


def normalize_series_to_sizes(
    series: pd.Series,
    source_min: float = 0.0,
    source_max: Optional[float] = None,
    target_min: float = 50.0,
    target_max: float = 1000.0,
    extra_nodes: Optional[Mapping[str, float]] = None,
) -> Dict[str, float]:
    """
    Normalize a metric series to a node-size dictionary for graph visualization.
    """
    numeric = pd.to_numeric(series, errors="coerce").fillna(0.0)

    if source_max is None:
        source_max = float(numeric.max()) if len(numeric) else 1.0

    if source_max == source_min:
        source_max = source_min + 1.0

    scaled = target_min + (target_max - target_min) * (numeric - source_min) / (source_max - source_min)
    sizes = {str(key): float(value) for key, value in scaled.to_dict().items()}

    if extra_nodes:
        sizes.update({str(key): float(value) for key, value in extra_nodes.items()})

    return sizes


def format_seconds_compact(seconds: float | int | None) -> str:
    """
    Format seconds as a compact human-readable label.
    """
    if seconds is None or pd.isna(seconds):
        return "0m"

    seconds = float(seconds)
    hours = int(seconds // 3600)
    minutes = int((seconds % 3600) // 60)

    parts = []
    if hours:
        parts.append(f"{hours}h")
    if minutes or not parts:
        parts.append(f"{minutes}m")
    return "".join(parts)


def build_node_labels(
    metric_series: pd.Series,
    short_label_map: Optional[Mapping[str, str]] = None,
    add_metric_text: bool = True,
) -> Dict[str, str]:
    """
    Build GraphML node labels from a KPI metric series.

    Example output:
        {'Sleeping': 'Sleeping 7h30m'}
    """
    labels: Dict[str, str] = {}
    short_label_map = short_label_map or {}

    for activity, value in metric_series.items():
        activity_str = str(activity)
        base_label = short_label_map.get(activity_str, activity_str)
        labels[activity_str] = (
            f"{base_label} {format_seconds_compact(value)}".strip()
            if add_metric_text
            else base_label
        )

    return labels


def save_dataframe(df: pd.DataFrame, output_path: str | Path, index: bool = True) -> Path:
    """
    Save a DataFrame based on the file extension.
    """
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    suffix = output_path.suffix.lower()
    if suffix == ".csv":
        df.to_csv(output_path, index=index)
    elif suffix in {".xlsx", ".xls"}:
        df.to_excel(output_path, index=index)
    elif suffix == ".parquet":
        df.to_parquet(output_path, index=index)
    else:
        raise ValueError(f"Unsupported output format: {suffix}")

    return output_path


# ---------------------------------------------------------------------------
# Differential baseline / day-comparison helpers
# ---------------------------------------------------------------------------

def compute_daily_activity_duration_table(
    df: pd.DataFrame,
    activity_col: str,
    day_col: str = "day",
    duration_col: str = "duration_seconds",
    fill_value: float = 0.0,
) -> pd.DataFrame:
    """
    Return a matrix of total duration per activity for each day.

    Rows   -> activities
    Columns-> days
    Values -> total duration in seconds on that day
    """
    required = {activity_col, day_col, duration_col}
    missing = required - set(df.columns)
    if missing:
        raise KeyError(f"Missing required columns: {sorted(missing)}")

    daily = (
        df.groupby([activity_col, day_col], dropna=False)[duration_col]
        .sum()
        .unstack(day_col)
        .fillna(fill_value)
        .sort_index(axis=1)
    )
    return daily


def compute_daily_activity_count_table(
    df: pd.DataFrame,
    activity_col: str,
    day_col: str = "day",
    fill_value: float = 0.0,
) -> pd.DataFrame:
    """
    Return a matrix of event counts per activity for each day.
    """
    required = {activity_col, day_col}
    missing = required - set(df.columns)
    if missing:
        raise KeyError(f"Missing required columns: {sorted(missing)}")

    counts = (
        df.groupby([activity_col, day_col], dropna=False)
        .size()
        .unstack(day_col)
        .fillna(fill_value)
        .sort_index(axis=1)
    )
    return counts


def align_series_to_index(series: pd.Series, index: pd.Index, fill_value: float = 0.0) -> pd.Series:
    """
    Align a Series to a target index and fill missing values.
    """
    aligned = pd.to_numeric(series, errors="coerce").reindex(index).fillna(fill_value)
    aligned.index = index
    return aligned


def compute_difference_from_baseline(
    daily_table: pd.DataFrame,
    baseline_series: pd.Series,
    fill_value: float = 0.0,
) -> pd.DataFrame:
    """
    Compute day-wise differences against a baseline Series.

    Returns a table with the same shape as daily_table:
        daily_value - baseline_value
    """
    aligned_baseline = align_series_to_index(baseline_series, daily_table.index, fill_value=fill_value)
    return daily_table.sub(aligned_baseline, axis=0)


def compute_relative_difference_from_baseline(
    daily_table: pd.DataFrame,
    baseline_series: pd.Series,
    zero_guard: float = 1.0,
) -> pd.DataFrame:
    """
    Compute relative differences against baseline:
        (daily - baseline) / max(abs(baseline), zero_guard)
    """
    aligned_baseline = align_series_to_index(baseline_series, daily_table.index, fill_value=0.0)
    denominator = aligned_baseline.abs().where(aligned_baseline.abs() > 0, other=float(zero_guard))
    return daily_table.sub(aligned_baseline, axis=0).div(denominator, axis=0)


def build_duration_difference_export_table(
    group_name: str,
    baseline_series: pd.Series,
    difference_table: pd.DataFrame,
    baseline_column_name: str = "Baseline_duration_seconds",
    day_prefix: str = "diff__",
) -> pd.DataFrame:
    """
    Build a wide export table for one group.

    Output columns:
        Group, Activity, Baseline_duration_seconds, diff__<day1>, diff__<day2>, ...
    """
    baseline_df = baseline_series.rename(baseline_column_name).to_frame()
    export_df = baseline_df.join(difference_table, how="outer").fillna(0.0).reset_index()
    export_df = export_df.rename(columns={export_df.columns[0]: "Activity"})
    export_df.insert(0, "Group", group_name)

    rename_map = {
        col: f"{day_prefix}{col}"
        for col in export_df.columns
        if col not in {"Group", "Activity", baseline_column_name}
    }
    export_df = export_df.rename(columns=rename_map)
    return export_df


def score_days_by_difference(
    difference_table: pd.DataFrame,
    baseline_series: Optional[pd.Series] = None,
    method: str = "mean_abs_relative",
    zero_guard: float = 1.0,
) -> pd.Series:
    """
    Score each day based on how different it is from the group's baseline.

    Supported methods
    -----------------
    mean_abs_relative:
        mean( abs(diff) / max(abs(baseline), floor) )
    sum_abs:
        sum( abs(diff) )
    euclidean:
        sqrt( sum( diff^2 ) )
    """
    if difference_table.empty:
        return pd.Series(dtype=float)

    method = method.lower()

    if method == "sum_abs":
        scores = difference_table.abs().sum(axis=0)

    elif method == "euclidean":
        scores = np.sqrt((difference_table.astype(float) ** 2).sum(axis=0))

    elif method == "mean_abs_relative":
        if baseline_series is None:
            raise ValueError("baseline_series is required when method='mean_abs_relative'.")

        aligned_baseline = align_series_to_index(baseline_series, difference_table.index, fill_value=0.0)
        non_zero_baseline = aligned_baseline[aligned_baseline.abs() > 0]
        floor = float(non_zero_baseline.median()) if not non_zero_baseline.empty else float(zero_guard)
        floor = max(floor, float(zero_guard))
        denominator = aligned_baseline.abs().where(aligned_baseline.abs() > 0, other=floor)
        scores = difference_table.abs().div(denominator, axis=0).mean(axis=0)

    else:
        raise ValueError(
            "Unsupported method. Use one of: 'mean_abs_relative', 'sum_abs', 'euclidean'."
        )

    scores = pd.to_numeric(scores, errors="coerce").fillna(0.0).sort_values(ascending=False)
    scores.name = "difference_score"
    return scores


def identify_most_different_days(
    difference_table: pd.DataFrame,
    baseline_series: Optional[pd.Series] = None,
    top_n: int = 2,
    method: str = "mean_abs_relative",
    zero_guard: float = 1.0,
    group_name: Optional[str] = None,
) -> pd.DataFrame:
    """
    Return the top-N days that differ most from baseline.
    """
    scores = score_days_by_difference(
        difference_table=difference_table,
        baseline_series=baseline_series,
        method=method,
        zero_guard=zero_guard,
    ).head(top_n)

    result = scores.reset_index()
    result.columns = ["Day", "difference_score"]
    if group_name is not None:
        result.insert(0, "Group", group_name)
    return result


def combine_day_difference_scores(
    score_series_map: Mapping[str, pd.Series],
    weights: Optional[Mapping[str, float]] = None,
    top_n: Optional[int] = None,
    group_name: Optional[str] = None,
) -> pd.DataFrame:
    """
    Combine multiple day-level difference score series into one ranked table.

    Example:
        combine_day_difference_scores(
            {
                "duration_score": duration_scores,
                "edge_score": edge_scores,
            },
            weights={"duration_score": 0.7, "edge_score": 0.3},
            top_n=2,
            group_name="Happy",
        )
    """
    if not score_series_map:
        return pd.DataFrame(columns=["Group", "Day", "combined_score"])

    weights = weights or {}
    combined_df = pd.DataFrame(index=pd.Index([], name="Day"))

    for metric_name, metric_series in score_series_map.items():
        metric_series = pd.to_numeric(metric_series, errors="coerce").fillna(0.0)
        combined_df = combined_df.join(metric_series.rename(metric_name), how="outer")

    combined_df = combined_df.fillna(0.0)

    combined_score = pd.Series(0.0, index=combined_df.index, name="combined_score")
    for column in combined_df.columns:
        weight = float(weights.get(column, 1.0))
        combined_score = combined_score.add(combined_df[column] * weight, fill_value=0.0)

    ranked = combined_df.copy()
    ranked["combined_score"] = combined_score
    ranked = ranked.sort_values("combined_score", ascending=False)

    if top_n is not None:
        ranked = ranked.head(top_n)

    ranked = ranked.reset_index()
    if group_name is not None:
        ranked.insert(0, "Group", group_name)
    return ranked


