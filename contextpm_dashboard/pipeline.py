# ContextPM - Contextualized Process Mining
# Copyright (C) 2026 Mohsen Shirali and Zahra Ahmadi (LIRIS, KU Leuven)
#
# This program is free software: you can redistribute it and/or modify it
# under the terms of the GNU General Public License version 3 as published by
# the Free Software Foundation. It is distributed WITHOUT ANY WARRANTY; see the
# LICENSE file or <https://www.gnu.org/licenses/gpl-3.0.html> for details.
"""
The contextualized process-mining pipeline, split into two stages.

Stage A - ``run_discovery``
    load -> clean activity names -> row filters -> duration/day columns ->
    split into groups -> KPI tables -> baselines -> directly-follows graphs.
    Everything that does not depend on styling; results stay in memory.

Stage B - ``run_export``
    node colours / labels / sizes / thresholds -> layout -> GraphML, GEXF, CSV
    and the JSON bundles used by the interactive viewer.

The functions mirror the cells of ``main_contextualization - eSense.ipynb``;
the numerical helpers are imported unchanged from ``Baseline_and_KPI.py`` and
``PM_in_Graph.py``.
"""
from __future__ import annotations

import datetime as _dt
import json
import logging
import math
import shutil
import warnings
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Tuple

import numpy as np
import pandas as pd
from pandas.api.types import is_bool_dtype, is_datetime64_any_dtype, is_numeric_dtype

from Baseline_and_KPI import (
    add_day_column,
    add_duration_seconds,
    apply_filters,
    build_duration_difference_export_table,
    build_node_labels,
    combine_day_difference_scores,
    compute_activity_summary,
    compute_average_duration_per_day,
    compute_average_event_count_per_day,
    compute_daily_activity_duration_table,
    compute_difference_from_baseline,
    compute_relative_difference_from_baseline,
    format_seconds_compact,
    load_table,
    normalize_series_to_sizes,
    save_dataframe,
    score_days_by_difference,
    split_dataframe_by_column,
    standardize_activity_names,
)
from PM_in_Graph import (
    add_artificial_start_end,
    build_differential_edge_weights,
    build_edge_heatmap_attributes,
    build_node_heatmap_attributes,
    build_simple_edge_attributes,
    compute_daily_edge_count_table,
    compute_readable_process_layout,
    discover_dfg_from_log,
    discover_direct_follows_counts_from_dataframe,
    edge_series_to_export_table,
    export_to_csv,
    export_to_gexf,
    export_to_graphml,
    filter_dfg_edges,
    merge_dfgs_max,
    pm4py,
    prepare_event_log,
)

from . import viewer_graphs as vg
from .config import resolve_path

log = logging.getLogger("contextpm.pipeline")

DFG = Dict[Tuple[str, str], float]
PROTECTED_OUTPUT_FOLDERS = {"figures", "edited"}


# ---------------------------------------------------------------------------
# Small helpers
# ---------------------------------------------------------------------------

def safe_slug(value: object) -> str:
    """File-system friendly version of a group / day name (same as the notebook)."""
    text = str(value)
    keep = [ch if ch.isalnum() or ch in {"-", "_"} else "_" for ch in text]
    return "".join(keep).strip("_") or "group"


def _blank(value: Any) -> bool:
    return value is None or (isinstance(value, str) and not value.strip())


def _jsonable(value: Any) -> Any:
    if value is None:
        return None
    if isinstance(value, (pd.Timestamp, _dt.datetime, _dt.date, _dt.time, pd.Timedelta, _dt.timedelta)):
        return str(value)
    if isinstance(value, np.integer):
        return int(value)
    if isinstance(value, np.floating):
        return None if np.isnan(value) else float(value)
    if isinstance(value, np.bool_):
        return bool(value)
    if isinstance(value, float) and (math.isnan(value) or math.isinf(value)):
        return None
    if isinstance(value, (str, int, float, bool)):
        return value
    return str(value)


def _records(frame: pd.DataFrame) -> List[Dict[str, Any]]:
    return [{str(k): _jsonable(v) for k, v in row.items()} for row in frame.to_dict(orient="records")]


def _rel(root: Path, path: Path) -> str:
    return Path(path).relative_to(root).as_posix()


# ---------------------------------------------------------------------------
# Dataset loading / description
# ---------------------------------------------------------------------------

def load_dataset(path: Path) -> pd.DataFrame:
    frame = load_table(path)
    frame.columns = [str(c) for c in frame.columns]
    return frame


def describe_dataframe(frame: pd.DataFrame, max_unique: int = 80) -> Dict[str, Any]:
    """Column overview for the dashboard (types, unique values, samples)."""
    columns = []
    for col in frame.columns:
        series = frame[col]
        n_unique = int(series.nunique(dropna=True))
        info: Dict[str, Any] = {
            "name": str(col),
            "dtype": str(series.dtype),
            "n_unique": n_unique,
            "n_missing": int(series.isna().sum()),
            "is_datetime": bool(is_datetime64_any_dtype(series)),
            "is_numeric": bool(is_numeric_dtype(series)) and not bool(is_bool_dtype(series)),
        }
        if n_unique <= max_unique:
            info["values"] = [_jsonable(v) for v in series.dropna().unique().tolist()]
        else:
            info["sample"] = [_jsonable(v) for v in series.dropna().head(5).tolist()]
        columns.append(info)
    return {"rows": int(len(frame)), "columns": columns}


# ---------------------------------------------------------------------------
# Stage A helpers
# ---------------------------------------------------------------------------

@dataclass
class ResolvedColumns:
    activity: str
    timestamp: str
    day: str
    case_id: str
    duration: str
    group: Optional[str] = None


def _match_values(series: pd.Series, wanted: Any) -> List[Any]:
    """Map values coming from the browser (strings) back onto the column's values."""
    lookup = {str(v): v for v in series.dropna().unique().tolist()}
    matched: List[Any] = []
    for value in list(wanted or []):
        if str(value) in lookup:
            matched.append(lookup[str(value)])
        else:
            matched.append(value)
    return matched


def _normalize_day_values(series: pd.Series) -> pd.Series:
    """Datetime-like day columns become plain dates (clean file names, same grouping)."""
    if is_datetime64_any_dtype(series):
        return series.dt.date
    if is_numeric_dtype(series) or is_bool_dtype(series):
        return series
    non_null = series.dropna()
    if non_null.empty:
        return series
    looks_like_date = non_null.astype(str).str.contains(r"[-/]", regex=True).all()
    if not looks_like_date:
        return series
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        converted = pd.to_datetime(series, errors="coerce")
    if (converted.notna() | series.isna()).all():
        return converted.dt.date
    return series


def prepare_dataframe(raw_df: pd.DataFrame, data: Mapping[str, Any]) -> Tuple[pd.DataFrame, ResolvedColumns]:
    """Notebook cell 'Load and prepare the activity data'."""
    frame = raw_df.copy()
    activity_col = data.get("activity_column")
    timestamp_col = data.get("timestamp_column")
    for label, col in (("Activity column", activity_col), ("Timestamp column", timestamp_col)):
        if _blank(col) or col not in frame.columns:
            raise ValueError(f"{label} '{col}' is not a column of the dataset.")

    # 1) standardize activity names ("Sleeping - Night - Good" -> "Sleeping")
    clean_col = data.get("activity_column_to_clean")
    if not _blank(clean_col):
        if clean_col in frame.columns:
            delimiter = data.get("clean_delimiter") or " - "
            keep_part = data.get("clean_keep_part") or "first"
            frame = standardize_activity_names(frame, activity_column=clean_col, delimiter=delimiter, keep_part=keep_part)
            log.info("Standardized '%s' (split on %r, kept the %s part).", clean_col, delimiter, keep_part)
        else:
            log.warning("Column to clean '%s' does not exist - skipped.", clean_col)

    # 2) row filters
    include: Dict[str, List[Any]] = {}
    exclude: Dict[str, List[Any]] = {}
    for rule in data.get("filters") or []:
        column = rule.get("column")
        values = rule.get("values") or []
        if _blank(column) or not values:
            continue
        if column not in frame.columns:
            log.warning("Filter column '%s' does not exist - rule skipped.", column)
            continue
        target = include if str(rule.get("mode", "exclude")).lower() == "include" else exclude
        target.setdefault(column, []).extend(_match_values(frame[column], values))
    before = len(frame)
    frame = apply_filters(frame, include_filters=include or None, exclude_filters=exclude or None)
    if include or exclude:
        log.info("Row filters applied: %d -> %d rows (include=%s, exclude=%s).", before, len(frame), include, exclude)
    if frame.empty:
        raise ValueError("No rows are left after applying the row filters.")

    # 3) duration in seconds
    duration_out = data.get("duration_output_column") or "duration_seconds"
    duration_col = data.get("duration_column")
    if not _blank(duration_col):
        if duration_col not in frame.columns:
            raise ValueError(f"Duration column '{duration_col}' is not a column of the dataset.")
        frame = add_duration_seconds(frame, duration_col=duration_col, output_col=duration_out)
        log.info("Duration (seconds) derived from '%s' -> '%s'.", duration_col, duration_out)
    else:
        start_col, end_col = data.get("start_column"), data.get("end_column")
        frame = add_duration_seconds(frame, start_col=start_col, end_col=end_col, output_col=duration_out)
        log.info("Duration (seconds) derived from '%s' and '%s' -> '%s'.", start_col, end_col, duration_out)
    missing_duration = int(frame[duration_out].isna().sum())
    if missing_duration:
        log.warning("%d rows have no duration value (treated as 0).", missing_duration)
        frame[duration_out] = frame[duration_out].fillna(0.0)

    # 4) day column
    day_col = data.get("day_column")
    if _blank(day_col):
        day_col = "day"
        frame = add_day_column(frame, timestamp_col=timestamp_col, output_col=day_col)
        log.info("Day column '%s' derived from '%s'.", day_col, timestamp_col)
    else:
        if day_col not in frame.columns:
            raise ValueError(f"Day column '{day_col}' is not a column of the dataset.")
        frame[day_col] = _normalize_day_values(frame[day_col])

    # 5) case id (one trace per day unless specified)
    case_col = data.get("case_id_column")
    if _blank(case_col):
        case_col = day_col
    elif case_col not in frame.columns:
        raise ValueError(f"Case-id column '{case_col}' is not a column of the dataset.")

    group_col = data.get("group_column")
    columns = ResolvedColumns(
        activity=str(activity_col),
        timestamp=str(timestamp_col),
        day=str(day_col),
        case_id=str(case_col),
        duration=str(duration_out),
        group=None if _blank(group_col) else str(group_col),
    )
    log.info(
        "Columns: activity=%s, timestamp=%s, day=%s, case=%s, duration=%s, group=%s",
        columns.activity, columns.timestamp, columns.day, columns.case_id, columns.duration, columns.group,
    )
    return frame, columns


def split_groups(frame: pd.DataFrame, data: Mapping[str, Any], columns: ResolvedColumns) -> Tuple[Dict[str, pd.DataFrame], pd.DataFrame]:
    """Split by the grouping column and keep only the selected columns."""
    if columns.group is None:
        groups = {"all_data": frame.copy()}
        log.info("No grouping column selected - a single group 'all_data' is used.")
    else:
        if columns.group not in frame.columns:
            raise ValueError(f"Grouping column '{columns.group}' is not a column of the dataset.")
        wanted = data.get("group_values")
        values = _match_values(frame[columns.group], wanted) if wanted else None
        split = split_dataframe_by_column(frame, column_name=columns.group, values=values, dropna=True)
        groups = {str(name): sub for name, sub in split.items() if len(sub)}
        if not groups:
            raise ValueError(f"The grouping column '{columns.group}' produced no groups.")
    overall = frame.copy()

    requested = [c for c in (data.get("columns_to_keep") or []) if isinstance(c, str)]
    missing = [c for c in requested if c not in frame.columns]
    if missing:
        log.warning("Selected columns not found in the dataset (ignored): %s", missing)
    keep = [c for c in requested if c in frame.columns]
    if keep:
        required = [columns.activity, columns.timestamp, columns.day, columns.case_id, columns.duration]
        if columns.group:
            required.append(columns.group)
        for col in required:
            if col not in keep:
                keep.append(col)
                log.info("Required column '%s' added to the kept columns.", col)
        groups = {name: sub[keep].copy() for name, sub in groups.items()}
        overall = overall[keep].copy()
        log.info("Columns kept: %s", keep)
    return groups, overall


def discover_dfg(frame: pd.DataFrame, columns: ResolvedColumns, start_label: str, end_label: str) -> Tuple[DFG, str]:
    """PM4Py discovery with the pandas fallback used by the notebook."""
    if pm4py is not None:
        try:
            with warnings.catch_warnings():
                warnings.simplefilter("ignore")
                event_log = prepare_event_log(
                    frame,
                    case_id_col=columns.case_id,
                    activity_col=columns.activity,
                    timestamp_col=columns.timestamp,
                )
                dfg, start_activities, end_activities = discover_dfg_from_log(event_log)
            merged = add_artificial_start_end(dfg, start_activities, end_activities, start_label=start_label, end_label=end_label)
            return merged, "pm4py"
        except Exception as exc:  # noqa: BLE001 - fall back exactly like the notebook
            log.warning("PM4Py discovery failed (%s: %s) - using the pandas implementation.", type(exc).__name__, exc)
    merged = discover_direct_follows_counts_from_dataframe(
        frame,
        case_id_col=columns.case_id,
        activity_col=columns.activity,
        timestamp_col=columns.timestamp,
        start_label=start_label,
        end_label=end_label,
        add_start_end_nodes=True,
    )
    return merged, "pandas"


def group_duration_baseline(frame: pd.DataFrame, group_name: str, columns: ResolvedColumns, overrides: Optional[Mapping[str, Any]]) -> pd.Series:
    baseline = compute_average_duration_per_day(frame, activity_col=columns.activity, day_col=columns.day, duration_col=columns.duration)
    group_overrides = (overrides or {}).get(group_name) or {}
    if group_overrides:
        for activity, value in group_overrides.items():
            baseline[str(activity)] = float(value)
        log.warning(
            "Duration baseline of group '%s' overridden for %d activities (analysis.duration_baseline_overrides).",
            group_name, len(group_overrides),
        )
    return baseline


# ---------------------------------------------------------------------------
# Stage A result containers
# ---------------------------------------------------------------------------

@dataclass
class GroupResult:
    name: str
    slug: str
    df: pd.DataFrame
    duration_baseline: pd.Series
    duration_daily_table: pd.DataFrame
    duration_difference_table: pd.DataFrame
    duration_relative_difference_table: pd.DataFrame
    duration_difference_export_table: pd.DataFrame
    activity_summary: pd.DataFrame
    avg_event_count_per_day: pd.Series
    edge_baseline: pd.Series
    edge_daily_table: pd.DataFrame
    edge_difference_table: pd.DataFrame
    top_days: pd.DataFrame
    merged_dfg: DFG
    days: List[Any] = field(default_factory=list)
    day_dfgs: Dict[str, DFG] = field(default_factory=dict)


@dataclass
class Discovery:
    dataset_label: str
    dataset_name: str
    output_dir: Path
    settings: Dict[str, Any]
    columns: ResolvedColumns
    start_label: str
    end_label: str
    raw_rows: int
    prepared_df: pd.DataFrame
    overall_df: pd.DataFrame
    overall_duration_baseline: pd.Series
    overall_edge_baseline: pd.Series
    overall_merged_dfg: DFG
    include_overall: bool
    groups: Dict[str, GroupResult]
    all_outlier_days: pd.DataFrame
    node_universe: List[str]
    engine: str
    created_at: str
    kpi_files: List[str] = field(default_factory=list)

    def summary(self) -> Dict[str, Any]:
        groups = []
        for result in self.groups.values():
            groups.append(
                {
                    "name": result.name,
                    "slug": result.slug,
                    "rows": int(len(result.df)),
                    "days": len(result.days),
                    "activities": int(len(result.duration_baseline)),
                    "transitions": len(result.merged_dfg),
                    "top_days": _records(result.top_days),
                }
            )
        return {
            "dataset": self.dataset_label,
            "dataset_name": self.dataset_name,
            "output_dir": str(self.output_dir),
            "engine": self.engine,
            "columns": asdict(self.columns),
            "rows": {"raw": self.raw_rows, "prepared": int(len(self.prepared_df))},
            "overall": {
                "included": self.include_overall,
                "rows": int(len(self.overall_df)),
                "days": int(self.overall_df[self.columns.day].nunique()),
                "activities": int(len(self.overall_duration_baseline)),
                "transitions": len(self.overall_merged_dfg),
            },
            "groups": groups,
            "outlier_days": _records(self.all_outlier_days),
            "node_universe": list(self.node_universe),
            "created_at": self.created_at,
            "kpi_files": list(self.kpi_files),
        }

    def node_metrics(self) -> Dict[str, Dict[str, Any]]:
        """Average duration per day of every node (overall and per group) for the UI."""
        metrics: Dict[str, Dict[str, Any]] = {}
        for node in self.node_universe:
            metrics[node] = {"overall": _jsonable(self.overall_duration_baseline.get(node))}
            for name, result in self.groups.items():
                metrics[node][name] = _jsonable(result.duration_baseline.get(node))
        return metrics


# ---------------------------------------------------------------------------
# Stage A
# ---------------------------------------------------------------------------

def _reset_output_dir(output_dir: Path) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    for child in output_dir.iterdir():
        if child.name in PROTECTED_OUTPUT_FOLDERS:
            continue
        if child.is_dir():
            shutil.rmtree(child, ignore_errors=True)
        else:
            try:
                child.unlink()
            except OSError:
                pass


def run_discovery(raw_df: pd.DataFrame, settings: Mapping[str, Any], config: Mapping[str, Any], dataset_label: str = "") -> Discovery:
    """Stage A: prepare the data, compute the KPIs and discover the process maps."""
    data = settings["data"]
    analysis = settings["analysis"]
    start_label = analysis.get("start_label") or "Start"
    end_label = analysis.get("end_label") or "End"
    dataset_name = safe_slug(data.get("dataset_name") or "dataset")
    output_dir = resolve_path(config["paths"]["outputs_dir"]) / dataset_name
    _reset_output_dir(output_dir)
    log.info("Dataset: %s (%d rows x %d columns)", dataset_label or "-", len(raw_df), raw_df.shape[1])
    log.info("Output folder: %s", output_dir)

    prepared, columns = prepare_dataframe(raw_df, data)
    groups_df, overall_df = split_groups(prepared, data, columns)
    include_overall = bool(data.get("include_overall_baseline", True))
    for name, frame in groups_df.items():
        log.info("Group '%s': %d rows, %d days.", name, len(frame), frame[columns.day].nunique())

    # ----- baseline (overall data) -----
    overall_duration_baseline = compute_average_duration_per_day(overall_df, activity_col=columns.activity, day_col=columns.day, duration_col=columns.duration)
    overall_edge_daily = compute_daily_edge_count_table(
        overall_df, day_col=columns.day, case_id_col=columns.case_id, activity_col=columns.activity,
        timestamp_col=columns.timestamp, start_label=start_label, end_label=end_label,
    )
    overall_edge_baseline = _edge_baseline_from_daily(overall_edge_daily)
    overall_dfg, engine = discover_dfg(overall_df, columns, start_label, end_label)
    log.info(
        "Baseline map (overall data): %d activities, %d transitions, %d days [engine: %s].",
        len(overall_duration_baseline), len(overall_dfg), overall_df[columns.day].nunique(), engine,
    )

    # ----- groups -----
    method = analysis.get("score_method") or "mean_abs_relative"
    weights = {
        "duration_score": float(analysis.get("duration_score_weight", 0.7)),
        "edge_score": float(analysis.get("edge_score_weight", 0.3)),
    }
    top_n = int(analysis.get("top_n_outlier_days") or 2)
    overrides = analysis.get("duration_baseline_overrides") or {}

    groups: Dict[str, GroupResult] = {}
    outlier_tables: List[pd.DataFrame] = []
    for name, frame in groups_df.items():
        log.info("Processing group '%s' ...", name)
        baseline = group_duration_baseline(frame, name, columns, overrides)
        daily_table = compute_daily_activity_duration_table(frame, activity_col=columns.activity, day_col=columns.day, duration_col=columns.duration)
        diff_table = compute_difference_from_baseline(daily_table, baseline)
        rel_diff_table = compute_relative_difference_from_baseline(daily_table, baseline)
        export_table = build_duration_difference_export_table(group_name=name, baseline_series=baseline, difference_table=diff_table)
        activity_summary = compute_activity_summary(frame, activity_col=columns.activity, duration_col=columns.duration)
        avg_event_count = compute_average_event_count_per_day(frame, activity_col=columns.activity, day_col=columns.day)

        edge_daily = compute_daily_edge_count_table(
            frame, day_col=columns.day, case_id_col=columns.case_id, activity_col=columns.activity,
            timestamp_col=columns.timestamp, start_label=start_label, end_label=end_label,
        )
        edge_baseline = _edge_baseline_from_daily(edge_daily)
        edge_diff = edge_daily.sub(edge_baseline, axis=0) if not edge_daily.empty else pd.DataFrame()

        duration_scores = score_days_by_difference(diff_table, baseline_series=baseline, method=method)
        edge_scores = (
            score_days_by_difference(edge_diff, baseline_series=edge_baseline, method=method)
            if not edge_diff.empty else pd.Series(dtype=float)
        )
        top_days = combine_day_difference_scores(
            {"duration_score": duration_scores, "edge_score": edge_scores},
            weights=weights, top_n=top_n, group_name=name,
        )
        outlier_tables.append(top_days)

        merged_dfg, _ = discover_dfg(frame, columns, start_label, end_label)
        days = list(daily_table.columns)
        day_dfgs: Dict[str, DFG] = {}
        for day_value in days:
            day_df = frame[frame[columns.day].astype(str) == str(day_value)]
            day_dfgs[str(day_value)] = discover_direct_follows_counts_from_dataframe(
                day_df, case_id_col=columns.case_id, activity_col=columns.activity, timestamp_col=columns.timestamp,
                start_label=start_label, end_label=end_label, add_start_end_nodes=True,
            )

        groups[name] = GroupResult(
            name=name, slug=safe_slug(name), df=frame,
            duration_baseline=baseline, duration_daily_table=daily_table,
            duration_difference_table=diff_table, duration_relative_difference_table=rel_diff_table,
            duration_difference_export_table=export_table, activity_summary=activity_summary,
            avg_event_count_per_day=avg_event_count, edge_baseline=edge_baseline,
            edge_daily_table=edge_daily, edge_difference_table=edge_diff, top_days=top_days,
            merged_dfg=merged_dfg, days=days, day_dfgs=day_dfgs,
        )
        top_text = ", ".join(str(v) for v in top_days.iloc[:, 1].tolist()) if len(top_days) else "-"
        log.info(
            "  %d activities, %d transitions, %d days. Most different days: %s",
            len(baseline), len(merged_dfg), len(days), top_text,
        )

    all_outliers = pd.concat(outlier_tables, ignore_index=True) if outlier_tables else pd.DataFrame()

    nodes = set(map(str, overall_duration_baseline.index)) | {str(n) for e in overall_dfg for n in e}
    for result in groups.values():
        nodes |= set(map(str, result.duration_baseline.index))
        nodes |= {str(n) for e in result.merged_dfg for n in e}
    nodes -= {start_label, end_label}
    node_universe = sorted(nodes) + [start_label, end_label]

    discovery = Discovery(
        dataset_label=dataset_label, dataset_name=dataset_name, output_dir=output_dir,
        settings={"data": json.loads(json.dumps(data, default=str)), "analysis": json.loads(json.dumps(analysis, default=str))},
        columns=columns, start_label=start_label, end_label=end_label, raw_rows=int(len(raw_df)),
        prepared_df=prepared, overall_df=overall_df, overall_duration_baseline=overall_duration_baseline,
        overall_edge_baseline=overall_edge_baseline, overall_merged_dfg=overall_dfg, include_overall=include_overall,
        groups=groups, all_outlier_days=all_outliers, node_universe=node_universe, engine=engine,
        created_at=_dt.datetime.now().isoformat(timespec="seconds"),
    )
    discovery.kpi_files = _write_kpi_files(discovery, top_n)
    log.info("Discovery finished: %d groups, %d distinct activities.", len(groups), len(node_universe) - 2)
    return discovery


def _edge_baseline_from_daily(edge_daily: pd.DataFrame) -> pd.Series:
    if edge_daily.empty:
        return pd.Series(dtype=float)
    baseline = edge_daily.mean(axis=1).sort_values(ascending=False)
    baseline.name = "avg_transition_count_per_day"
    return baseline


def _write_kpi_files(discovery: Discovery, top_n: int) -> List[str]:
    """CSV exports of the KPI / baseline / differential tables (notebook cell 4)."""
    out = discovery.output_dir
    written: List[Path] = []

    prepared_path = out / "prepared_data.csv"
    save_dataframe(discovery.prepared_df, prepared_path, index=False)
    written.append(prepared_path)

    if len(discovery.all_outlier_days):
        path = out / f"all_groups_top_{top_n}_days.csv"
        save_dataframe(discovery.all_outlier_days, path, index=False)
        written.append(path)

    overall_dir = out / "overall_data"
    overall_dir.mkdir(parents=True, exist_ok=True)
    path = overall_dir / "overall_data_avg_duration_per_day.csv"
    discovery.overall_duration_baseline.to_csv(path, header=["avg_duration_seconds"])
    written.append(path)
    if not discovery.overall_edge_baseline.empty:
        path = overall_dir / "overall_data_avg_transitions_per_day.csv"
        discovery.overall_edge_baseline.to_csv(path, header=["avg_transition_count_per_day"])
        written.append(path)

    for result in discovery.groups.values():
        gdir = out / result.slug
        gdir.mkdir(parents=True, exist_ok=True)
        slug = result.slug
        written.append(save_dataframe(result.activity_summary, gdir / f"{slug}_activity_summary.csv", index=False))
        path = gdir / f"{slug}_avg_event_count_per_day.csv"
        result.avg_event_count_per_day.to_csv(path, header=["avg_event_count"])
        written.append(path)
        path = gdir / f"{slug}_avg_duration_per_day.csv"
        result.duration_baseline.to_csv(path, header=["avg_duration_seconds"])
        written.append(path)
        written.append(save_dataframe(result.duration_difference_export_table, gdir / f"{slug}_duration_difference_vs_baseline.csv", index=False))
        if not result.edge_difference_table.empty:
            edge_export = edge_series_to_export_table(group_name=result.name, baseline_series=result.edge_baseline, difference_table=result.edge_difference_table)
            written.append(save_dataframe(edge_export, gdir / f"{slug}_edge_difference_vs_baseline.csv", index=False))
        written.append(save_dataframe(result.top_days, gdir / f"{slug}_top_{top_n}_days.csv", index=False))

    with open(out / "discovery_settings.json", "w", encoding="utf-8") as handle:
        json.dump(discovery.settings, handle, indent=2, default=str)
    log.info("KPI tables written: %d files.", len(written))
    return [_rel(out, p) for p in written]


# ---------------------------------------------------------------------------
# Stage B helpers (styling)
# ---------------------------------------------------------------------------

def resolve_node_color(node: str, style: Mapping[str, Any], start_end: Tuple[str, str]) -> Tuple[str, str, str]:
    """(fill colour, border colour, source) for a node of a baseline / group map."""
    colors = style.get("node_colors") or {}
    default_fill = colors.get("default", "#A3A3A3")
    default_border = colors.get("default_border", "#ffffff")
    if node in start_end:
        return colors.get("start_end", "#d9d9d9"), default_border, "start_end"
    explicit = colors.get("explicit") or {}
    if node in explicit and not _blank(explicit[node]):
        return str(explicit[node]), str(explicit[node]), "explicit"
    lowered = node.lower()
    for rule in colors.get("keyword_rules") or []:
        keyword = str(rule.get("keyword", "")).lower().strip()
        color = rule.get("color")
        if keyword and keyword in lowered and not _blank(color):
            return str(color), str(color), f"keyword:{keyword}"
    return default_fill, default_border, "default"


def build_categorical_node_attributes(values: pd.Series, style: Mapping[str, Any], start_end: Tuple[str, str]) -> Dict[str, Dict[str, Any]]:
    """Generalization of ``build_keyword_baseline_node_attributes`` driven by the settings."""
    all_nodes = sorted(set(map(str, values.index)) | set(start_end))
    aligned = pd.to_numeric(values, errors="coerce").reindex(all_nodes).fillna(0.0)
    attributes: Dict[str, Dict[str, Any]] = {}
    for node in all_nodes:
        fill, border, _ = resolve_node_color(node, style, start_end)
        metric = float(aligned.loc[node])
        attributes[node] = {
            "fill_color": fill,
            "border_color": border,
            "border_width": 1.0,
            "baseline_value": metric,
            "heatmap_value": metric,
            "heatmap_mode": "baseline_keyword",
        }
    return attributes


def _series_range(series: pd.Series, positive_only: bool = True) -> Tuple[float, float]:
    numeric = pd.to_numeric(series, errors="coerce").dropna()
    if positive_only:
        numeric = numeric[numeric > 0]
    if numeric.empty:
        return 0.0, 1.0
    return float(numeric.min()), float(numeric.max())


def _legend_categorical(node_attributes, base_labels, start_end, edge_series, node_series, style, visible_nodes=None) -> Dict[str, Any]:
    by_color: Dict[str, List[str]] = {}
    for node, attrs in node_attributes.items():
        if node in start_end or (visible_nodes is not None and node not in visible_nodes):
            continue
        by_color.setdefault(str(attrs.get("fill_color", "#bdbdbd")).lower(), []).append(base_labels.get(node, node))
    entries = [{"color": color, "label": ", ".join(sorted(names))} for color, names in by_color.items()]
    entries.sort(key=lambda e: -len(e["label"].split(", ")))
    entries.append({"color": (style.get("node_colors") or {}).get("start_end", "#d9d9d9"), "label": f"{start_end[0]} / {start_end[1]}"})
    edge_min, edge_max = _series_range(edge_series)
    node_min, node_max = _series_range(node_series)
    return {
        "node_color": {"type": "categorical", "title": "Node colour = activity", "entries": entries},
        "edge_width": {"type": "range", "title": "Edge thickness = average transitions per day", "min": edge_min, "max": edge_max, "unit": "/day", "format": "number"},
        "node_size": {"type": "range", "title": "Node size = average duration per day", "min": node_min, "max": node_max, "unit": "", "format": "duration"},
    }


def _legend_diverging(node_max_seconds, edge_max, node_title, edge_title, width_title, width_min, width_max, width_unit, node_series, colors) -> Dict[str, Any]:
    node_min, node_max = _series_range(node_series)
    return {
        "node_color": {"type": "diverging", "title": node_title, "min": -node_max_seconds / 60.0, "max": node_max_seconds / 60.0, "unit": "min", "colors": list(colors)},
        "edge_color": {"type": "diverging", "title": edge_title, "min": -edge_max, "max": edge_max, "unit": "/day", "colors": list(colors)},
        "edge_width": {"type": "range", "title": width_title, "min": width_min, "max": width_max, "unit": width_unit, "format": "number"},
        "node_size": {"type": "range", "title": "Node size = duration", "min": node_min, "max": node_max, "unit": "", "format": "duration"},
    }


def _max_abs_difference(a: pd.Series, b: pd.Series) -> float:
    index = sorted(set(map(str, a.index)) | set(map(str, b.index))) if not isinstance(a.index, pd.MultiIndex) else sorted(set(a.index) | set(b.index))
    if not index:
        return 0.0
    aligned_a = pd.to_numeric(a, errors="coerce").reindex(index).fillna(0.0)
    aligned_b = pd.to_numeric(b, errors="coerce").reindex(index).fillna(0.0)
    return float((aligned_a - aligned_b).abs().max())


def _clear_graph_outputs(discovery: Discovery) -> None:
    out = discovery.output_dir
    # The overall_data folder also holds KPI tables, so only the graph files are removed there.
    for path in (out / "overall_data").glob("overall_data__*"):
        try:
            path.unlink()
        except OSError:
            pass
    for result in discovery.groups.values():
        for folder in ("simple_group_graph", "differential_vs_overall", "days"):
            shutil.rmtree(out / result.slug / folder, ignore_errors=True)


# ---------------------------------------------------------------------------
# Stage B
# ---------------------------------------------------------------------------

class _Exporter:
    """Shared state for exporting one run (keeps ``run_export`` readable)."""

    def __init__(self, discovery: Discovery, settings: Mapping[str, Any]):
        self.discovery = discovery
        self.graph = settings["graph"]
        self.style = settings["style"]
        self.out = discovery.output_dir
        self.start = discovery.start_label
        self.end = discovery.end_label
        self.start_end = (self.start, self.end)
        threshold = self.graph.get("edge_threshold")
        self.threshold = None if _blank(threshold) else int(threshold)
        self.keep_loops = bool(self.graph.get("keep_self_loops", True))
        self.export_gexf = bool(self.graph.get("export_gexf", True))
        self.min_weight = float(self.graph.get("min_edge_weight", 0.001))
        self.add_metric = bool(self.graph.get("add_metric_text_to_labels", True))
        self.label_map = {str(k): str(v) for k, v in (self.style.get("node_labels") or {}).items()}
        diff = self.style.get("differential") or {}
        self.neg = diff.get("negative_color", "#ff0000")
        self.zero = diff.get("zero_color", "#a3a3a3")
        self.pos = diff.get("positive_color", "#006400")
        self.node_near_zero = float(diff.get("node_near_zero_ratio", 0.05))
        self.edge_near_zero = float(diff.get("edge_near_zero_ratio", 0.05))
        self.node_border = diff.get("node_border_color", "#000000")
        self.day_edge_zero = diff.get("day_edge_zero_color") or self.zero
        self.start_end_fill = (self.style.get("node_colors") or {}).get("start_end", "#d9d9d9")
        self.simple_edge_color = self.style.get("simple_edge_color", "#808080")
        self.hidden = {str(n) for n in (self.style.get("hidden_nodes") or [])} - set(self.start_end)
        size = float(self.graph.get("start_end_node_size", 100))
        self.size_kwargs = dict(
            source_min=float(self.graph.get("node_size_source_min", 0)),
            source_max=float(self.graph.get("node_size_source_max", 86400)),
            target_min=float(self.graph.get("node_size_target_min", 50)),
            target_max=float(self.graph.get("node_size_target_max", 1000)),
            extra_nodes={self.start: size, self.end: size},
        )
        self.graphs: List[Dict[str, Any]] = []

    # -- helpers -----------------------------------------------------------
    def filt(self, dfg: DFG) -> DFG:
        """Drop the hidden nodes, then keep the strongest edges around each node."""
        visible = {(s, t): w for (s, t), w in dfg.items() if s not in self.hidden and t not in self.hidden}
        if self.threshold is None:
            return visible
        return filter_dfg_edges(visible, threshold=self.threshold, keep_self_loops=self.keep_loops)

    def labels_for(self, series: pd.Series) -> Dict[str, str]:
        labels = build_node_labels(series, short_label_map=self.label_map, add_metric_text=self.add_metric)
        labels[self.start] = self.label_map.get(self.start, self.start)
        labels[self.end] = self.label_map.get(self.end, self.end)
        return labels

    def base_labels(self, nodes) -> Dict[str, str]:
        return {str(n): self.label_map.get(str(n), str(n)) for n in nodes}

    def layout(self, dfg: DFG) -> Dict[str, Tuple[float, float]]:
        return compute_readable_process_layout(
            dfg, start_label=self.start, end_label=self.end,
            x_gap=float(self.graph.get("layout_x_gap", 620)), y_gap=float(self.graph.get("layout_y_gap", 240)),
        )

    def simple_weights(self, dfg: DFG, series: pd.Series) -> DFG:
        return {(str(s), str(t)): max(float(series.get((s, t), 0.0)), self.min_weight) for (s, t) in dfg.keys()}

    def export(self, stem: Path, *, kind: str, name: str, group: Optional[str], day: Optional[str], dfg: DFG,
               node_sizes, node_labels, edge_weights, node_attributes, edge_attributes, positions, legend,
               export_csv: bool = False, description: str = "") -> Dict[str, Any]:
        stem.parent.mkdir(parents=True, exist_ok=True)
        common = dict(dfg=dfg, node_sizes=node_sizes, node_labels=node_labels, edge_weights=edge_weights,
                      node_attributes=node_attributes, edge_attributes=edge_attributes)
        files: Dict[str, str] = {}
        files["graphml"] = _rel(self.out, export_to_graphml(node_positions=positions, output_file=stem.parent / f"{stem.name}.graphml", **common))
        if self.export_gexf:
            files["gexf"] = _rel(self.out, export_to_gexf(node_positions=positions, output_file=stem.parent / f"{stem.name}.gexf", **common))
        if export_csv:
            nodes_csv, edges_csv = export_to_csv(nodes_file=stem.parent / f"{stem.name}_nodes.csv", edges_file=stem.parent / f"{stem.name}_edges.csv", **common)
            files["nodes_csv"], files["edges_csv"] = _rel(self.out, nodes_csv), _rel(self.out, edges_csv)
        json_path = stem.parent / f"{stem.name}.json"
        files["json"] = _rel(self.out, json_path)
        bundle = vg.build_bundle(
            graph_id=_rel(self.out, stem), name=name, kind=kind, group=group, day=day, legend=legend, files=files,
            node_positions=positions, base_labels=self.base_labels({n for e in dfg for n in e}), description=description, **common,
        )
        vg.write_bundle(bundle, json_path)
        summary = vg.summarize(bundle, files["json"])
        self.graphs.append(summary)
        return summary

    # -- graphs ------------------------------------------------------------
    def export_overall(self) -> None:
        d = self.discovery
        odir = self.out / "overall_data"
        dfg = self.filt(d.overall_merged_dfg)
        node_attrs = build_categorical_node_attributes(d.overall_duration_baseline, self.style, self.start_end)
        edge_attrs = build_simple_edge_attributes(d.overall_edge_baseline, default_color=self.simple_edge_color)
        legend = _legend_categorical(node_attrs, self.base_labels(node_attrs), self.start_end, d.overall_edge_baseline, d.overall_duration_baseline, self.style, visible_nodes={n for e in dfg for n in e})
        self.export(
            odir / "overall_data__baseline", kind="baseline", name="Baseline - overall data", group=None, day=None, dfg=dfg,
            node_sizes=normalize_series_to_sizes(d.overall_duration_baseline, **self.size_kwargs),
            node_labels=self.labels_for(d.overall_duration_baseline),
            edge_weights=self.simple_weights(dfg, d.overall_edge_baseline),
            node_attributes=node_attrs, edge_attributes=edge_attrs, positions=self.layout(dfg), legend=legend,
            description="Process map of all rows; node size = average duration per day, edge thickness = average transitions per day.",
        )
        log.info("Baseline map exported: %d nodes, %d edges.", len({n for e in dfg for n in e}), len(dfg))

    def export_group(self, result: GroupResult) -> None:
        d = self.discovery
        name, slug = result.name, result.slug
        gdir = self.out / slug
        node_max_abs_diff = float(result.duration_difference_table.abs().max().max()) if not result.duration_difference_table.empty else 0.0
        edge_max_abs_diff = float(result.edge_difference_table.abs().max().max()) if not result.edge_difference_table.empty else 0.0

        # ---- simple group map ----
        sdir = gdir / "simple_group_graph"
        simple_dfg = self.filt(result.merged_dfg)
        node_attrs = build_categorical_node_attributes(result.duration_baseline, self.style, self.start_end)
        edge_attrs = build_simple_edge_attributes(result.edge_baseline, default_color=self.simple_edge_color)
        legend = _legend_categorical(node_attrs, self.base_labels(node_attrs), self.start_end, result.edge_baseline, result.duration_baseline, self.style, visible_nodes={n for e in simple_dfg for n in e})
        self.export(
            sdir / f"{slug}__simple", kind="simple", name=f"{name} - group map", group=name, day=None, dfg=simple_dfg,
            node_sizes=normalize_series_to_sizes(result.duration_baseline, **self.size_kwargs),
            node_labels=self.labels_for(result.duration_baseline),
            edge_weights=self.simple_weights(simple_dfg, result.edge_baseline),
            node_attributes=node_attrs, edge_attributes=edge_attrs, positions=self.layout(simple_dfg), legend=legend,
            description=f"Process map of the '{name}' rows; node size = average duration per day, edge thickness = average transitions per day.",
        )
        log.info("Group map '%s' exported: %d nodes, %d edges.", name, len({n for e in simple_dfg for n in e}), len(simple_dfg))

        # ---- differential map: group baseline vs overall baseline ----
        if d.include_overall:
            ddir = gdir / "differential_vs_overall"
            diff_dfg = self.filt(merge_dfgs_max(result.merged_dfg, d.overall_merged_dfg))
            size_series = pd.concat(
                [pd.to_numeric(result.duration_baseline, errors="coerce"), pd.to_numeric(d.overall_duration_baseline, errors="coerce")],
                axis=1,
            ).fillna(0.0).max(axis=1)
            label_series = pd.to_numeric(result.duration_baseline, errors="coerce").reindex(size_series.index).fillna(0.0)
            diff_node_attrs = build_node_heatmap_attributes(
                day_values=result.duration_baseline, baseline_values=d.overall_duration_baseline, max_abs_difference=None,
                white_border_color=self.node_border, near_zero_ratio=self.node_near_zero, start_end_nodes=self.start_end,
                start_end_fill_color=self.start_end_fill, negative_color=self.neg, zero_color=self.zero, positive_color=self.pos,
            )
            diff_edge_attrs = build_edge_heatmap_attributes(
                day_values=result.edge_baseline, baseline_values=d.overall_edge_baseline, max_abs_difference=None,
                near_zero_ratio=self.edge_near_zero, negative_color=self.neg, zero_color=self.zero, positive_color=self.pos,
            )
            diff_edge_weights = build_differential_edge_weights(result.edge_baseline, d.overall_edge_baseline, min_weight=self.min_weight)
            node_diff_max = _max_abs_difference(result.duration_baseline, d.overall_duration_baseline)
            edge_diff_max = _max_abs_difference(result.edge_baseline, d.overall_edge_baseline)
            legend = _legend_diverging(
                node_diff_max, edge_diff_max,
                node_title=f"Node colour = duration difference ({name} - overall)",
                edge_title=f"Edge colour = transitions/day difference ({name} - overall)",
                width_title="Edge thickness = |transitions/day difference|", width_min=0.0, width_max=edge_diff_max, width_unit="/day",
                node_series=size_series, colors=(self.neg, self.zero, self.pos),
            )
            self.export(
                ddir / f"{slug}__differential_vs_overall", kind="differential", name=f"{name} - differential vs. overall", group=name, day=None,
                dfg=diff_dfg, node_sizes=normalize_series_to_sizes(size_series, **self.size_kwargs), node_labels=self.labels_for(label_series),
                edge_weights=diff_edge_weights, node_attributes=diff_node_attrs, edge_attributes=diff_edge_attrs,
                positions=self.layout(diff_dfg), legend=legend,
                description=f"'{name}' baseline compared with the overall baseline: red = below, green = above the overall values.",
            )
            log.info("Differential map '%s vs overall' exported: %d nodes, %d edges.", name, len({n for e in diff_dfg for n in e}), len(diff_dfg))

        # ---- one map per day (day vs group baseline) ----
        if bool(self.graph.get("export_day_graphs", True)):
            daydir = gdir / "days"
            export_csv = bool(self.graph.get("export_csv_for_days", True))
            for day_value in result.days:
                day_key = str(day_value)
                day_slug = safe_slug(day_value)
                dfg = self.filt(merge_dfgs_max(result.merged_dfg, result.day_dfgs.get(day_key, {})))
                day_nodes = sorted({str(n) for e in dfg for n in e if str(n) not in self.start_end})
                day_series = pd.to_numeric(result.duration_daily_table[day_value], errors="coerce").reindex(day_nodes).fillna(0.0)
                node_attrs = build_node_heatmap_attributes(
                    day_values=result.duration_daily_table[day_value], baseline_values=result.duration_baseline,
                    max_abs_difference=node_max_abs_diff, white_border_color=self.node_border, near_zero_ratio=self.node_near_zero,
                    start_end_nodes=self.start_end, start_end_fill_color=self.start_end_fill,
                    negative_color=self.neg, zero_color=self.zero, positive_color=self.pos,
                )
                if not result.edge_daily_table.empty and day_value in result.edge_daily_table.columns:
                    day_edges = result.edge_daily_table[day_value]
                else:
                    day_edges = pd.Series(dtype=float)
                edge_attrs = build_edge_heatmap_attributes(
                    day_values=day_edges, baseline_values=result.edge_baseline, max_abs_difference=edge_max_abs_diff,
                    near_zero_ratio=self.edge_near_zero, negative_color=self.neg, zero_color=self.day_edge_zero, positive_color=self.pos,
                )
                wmin, wmax = _series_range(day_edges)
                legend = _legend_diverging(
                    node_max_abs_diff, edge_max_abs_diff,
                    node_title=f"Node colour = duration difference (this day - {name} baseline)",
                    edge_title=f"Edge colour = transitions difference (this day - {name} baseline)",
                    width_title="Edge thickness = transitions on this day", width_min=wmin, width_max=wmax, width_unit="",
                    node_series=day_series, colors=(self.neg, self.zero, self.pos),
                )
                legend["edge_color"]["colors"] = [self.neg, self.day_edge_zero, self.pos]
                self.export(
                    daydir / f"{slug}__{day_slug}", kind="day", name=f"{name} - {day_key}", group=name, day=day_key, dfg=dfg,
                    node_sizes=normalize_series_to_sizes(day_series, **self.size_kwargs), node_labels=self.labels_for(day_series),
                    edge_weights=self.simple_weights(dfg, day_edges), node_attributes=node_attrs, edge_attributes=edge_attrs,
                    positions=self.layout(dfg), legend=legend, export_csv=export_csv,
                    description=f"Day {day_key} of group '{name}' compared with the group's baseline.",
                )
            log.info("Day maps of '%s' exported: %d days.", name, len(result.days))


def run_export(discovery: Discovery, settings: Mapping[str, Any]) -> Dict[str, Any]:
    """Stage B: apply the styling options and write all graph files."""
    _clear_graph_outputs(discovery)
    exporter = _Exporter(discovery, settings)
    log.info(
        "Building graph files (edge threshold=%s, self loops=%s, GEXF=%s, day maps=%s, hidden nodes=%d) ...",
        exporter.threshold, exporter.keep_loops, exporter.export_gexf, bool(exporter.graph.get("export_day_graphs", True)), len(exporter.hidden),
    )
    if discovery.include_overall:
        exporter.export_overall()
    for result in discovery.groups.values():
        exporter.export_group(result)
    with open(discovery.output_dir / "graph_settings.json", "w", encoding="utf-8") as handle:
        json.dump({"graph": settings["graph"], "style": settings["style"]}, handle, indent=2, default=str)
    log.info("Graph export finished: %d graphs written to %s", len(exporter.graphs), discovery.output_dir)
    return {"graphs": exporter.graphs, "count": len(exporter.graphs), "output_dir": str(discovery.output_dir)}


def default_node_table(discovery: Optional[Discovery], settings: Mapping[str, Any]) -> List[Dict[str, Any]]:
    """Rows for the 'Node colours' and 'Node labels' tables of the styling page."""
    style = settings["style"]
    start = settings["analysis"].get("start_label") or "Start"
    end = settings["analysis"].get("end_label") or "End"
    nodes = list(discovery.node_universe) if discovery else [start, end]
    metrics = discovery.node_metrics() if discovery else {}
    labels = style.get("node_labels") or {}
    hidden = {str(n) for n in (style.get("hidden_nodes") or [])}
    rows = []
    for node in nodes:
        fill, _, source = resolve_node_color(node, style, (start, end))
        overall_seconds = (metrics.get(node) or {}).get("overall")
        rows.append(
            {
                "id": node,
                "label": labels.get(node, node),
                "color": fill,
                "color_source": source,
                "is_start_end": node in (start, end),
                "hidden": node in hidden and node not in (start, end),
                "avg_duration_seconds": overall_seconds,
                "avg_duration_text": format_seconds_compact(overall_seconds) if overall_seconds is not None else "",
                "groups": {k: v for k, v in (metrics.get(node) or {}).items() if k != "overall"},
            }
        )
    return rows
