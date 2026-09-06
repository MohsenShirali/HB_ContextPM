# ContextPM - Contextualized Process Mining
# Copyright (C) 2026 Mohsen Shirali and Zahra Ahmadi (LIRIS, KU Leuven)
#
# This program is free software: you can redistribute it and/or modify it
# under the terms of the GNU General Public License version 3 as published by
# the Free Software Foundation. It is distributed WITHOUT ANY WARRANTY; see the
# LICENSE file or <https://www.gnu.org/licenses/gpl-3.0.html> for details.
from __future__ import annotations

from collections import defaultdict, deque
from pathlib import Path
from typing import Any, Dict, Iterable, Mapping, Optional, Tuple

import networkx as nx
import pandas as pd

try:
    import pm4py  # type: ignore
except ImportError:  # pragma: no cover - depends on the user's environment
    pm4py = None


DFGType = Dict[Tuple[str, str], float]


def _clean_graph_attrs_for_graphml(attrs: Mapping[str, Any]) -> Dict[str, Any]:
    """
    Keep only GraphML-safe scalar attributes and drop missing values.

    NetworkX GraphML export does not accept None values. This helper removes
    None / NaN / pandas NA values and converts pandas timestamps or numpy
    scalars into plain Python values before export.
    """
    cleaned: Dict[str, Any] = {}

    for key, value in attrs.items():
        if value is None:
            continue

        if isinstance(value, pd.Timestamp):
            cleaned[str(key)] = value.isoformat()
            continue

        if hasattr(value, "item") and not isinstance(value, (str, bytes, bool)):
            try:
                value = value.item()
            except Exception:
                pass

        try:
            if pd.isna(value):
                continue
        except Exception:
            pass

        if isinstance(value, (str, int, float, bool)):
            cleaned[str(key)] = value
        else:
            cleaned[str(key)] = str(value)

    return cleaned


def _require_pm4py() -> None:
    if pm4py is None:
        raise ImportError(
            "pm4py is required for the process-mining functions in PMinGraph.py. "
            "Install it in your environment with: pip install pm4py"
        )


def prepare_event_log(
    df: pd.DataFrame,
    case_id_col: str = "CaseID",
    activity_col: str = "Activity-level 2",
    timestamp_col: str = "StartTime",
    sort_log: bool = True,
) -> pd.DataFrame:
    """
    Convert a DataFrame into a PM4Py-compatible event log DataFrame.
    """
    _require_pm4py()

    required = {case_id_col, activity_col, timestamp_col}
    missing = required - set(df.columns)
    if missing:
        raise KeyError(f"Missing required columns: {sorted(missing)}")

    working_df = df.copy()
    working_df[timestamp_col] = pd.to_datetime(working_df[timestamp_col], errors="coerce")

    if sort_log:
        working_df = working_df.sort_values([case_id_col, timestamp_col])

    log = pm4py.format_dataframe(
        working_df,
        case_id=case_id_col,
        activity_key=activity_col,
        timestamp_key=timestamp_col,
    )
    return log


def discover_dfg_from_log(log: pd.DataFrame):
    """
    Discover the directly-follows graph (DFG) and the start/end activity frequencies.
    """
    _require_pm4py()

    from pm4py.discovery import discover_dfg

    dfg, start_activities, end_activities = discover_dfg(log)
    return dfg, start_activities, end_activities


def add_artificial_start_end(
    dfg: Mapping[Tuple[str, str], float],
    start_activities: Mapping[str, float],
    end_activities: Mapping[str, float],
    start_label: str = "Start",
    end_label: str = "End",
) -> DFGType:
    """
    Add artificial start and end nodes to the discovered DFG.
    """
    merged_dfg: DFGType = {tuple(map(str, edge)): float(weight) for edge, weight in dfg.items()}

    for activity, weight in start_activities.items():
        merged_dfg[(start_label, str(activity))] = float(weight)

    for activity, weight in end_activities.items():
        merged_dfg[(str(activity), end_label)] = float(weight)

    return merged_dfg


def discover_process_graph_from_dataframe(
    df: pd.DataFrame,
    case_id_col: str = "CaseID",
    activity_col: str = "Activity-level 2",
    timestamp_col: str = "StartTime",
) -> tuple[pd.DataFrame, DFGType, Dict[str, float], Dict[str, float], DFGType]:
    """
    Full PM4Py pipeline for:
        DataFrame -> Event log -> DFG -> artificial Start/End nodes
    """
    log = prepare_event_log(
        df=df,
        case_id_col=case_id_col,
        activity_col=activity_col,
        timestamp_col=timestamp_col,
    )
    dfg, start_activities, end_activities = discover_dfg_from_log(log)
    merged_dfg = add_artificial_start_end(dfg, start_activities, end_activities)
    return log, dfg, dict(start_activities), dict(end_activities), merged_dfg


# ---------------------------------------------------------------------------
# Pure-pandas DFG helpers for daily / baseline comparisons
# ---------------------------------------------------------------------------

def discover_direct_follows_counts_from_dataframe(
    df: pd.DataFrame,
    case_id_col: str = "CaseID",
    activity_col: str = "Activity-level 2",
    timestamp_col: str = "StartTime",
    start_label: str = "Start",
    end_label: str = "End",
    add_start_end_nodes: bool = True,
) -> DFGType:
    """
    Discover a directly-follows graph from a DataFrame without requiring PM4Py.

    This is useful for day-wise differential edge analysis and mirrors the
    PM4Py directly-follows logic closely for ordered event data.
    """
    required = {case_id_col, activity_col, timestamp_col}
    missing = required - set(df.columns)
    if missing:
        raise KeyError(f"Missing required columns: {sorted(missing)}")

    working_df = df.copy()
    working_df[timestamp_col] = pd.to_datetime(working_df[timestamp_col], errors="coerce")
    working_df = working_df.dropna(subset=[case_id_col, activity_col, timestamp_col])
    working_df = working_df.sort_values([case_id_col, timestamp_col]).reset_index(drop=True)

    if working_df.empty:
        return {}

    edge_counts: DFGType = defaultdict(float)

    working_df["_next_activity"] = working_df.groupby(case_id_col, dropna=False)[activity_col].shift(-1)
    transitions = working_df.dropna(subset=["_next_activity"])

    for row in transitions[[activity_col, "_next_activity"]].itertuples(index=False):
        edge_counts[(str(row[0]), str(row[1]))] += 1.0

    if add_start_end_nodes:
        first_rows = working_df.groupby(case_id_col, dropna=False, as_index=False).first()
        last_rows = working_df.groupby(case_id_col, dropna=False, as_index=False).last()

        for activity in first_rows[activity_col]:
            edge_counts[(start_label, str(activity))] += 1.0

        for activity in last_rows[activity_col]:
            edge_counts[(str(activity), end_label)] += 1.0

    return dict(edge_counts)


def compute_daily_edge_count_table(
    df: pd.DataFrame,
    day_col: str,
    case_id_col: str = "CaseID",
    activity_col: str = "Activity-level 2",
    timestamp_col: str = "StartTime",
    start_label: str = "Start",
    end_label: str = "End",
) -> pd.DataFrame:
    """
    Build a per-day transition-count matrix.

    Rows   -> edge tuples (source, target)
    Columns-> days
    Values -> transition count on that day
    """
    required = {day_col, case_id_col, activity_col, timestamp_col}
    missing = required - set(df.columns)
    if missing:
        raise KeyError(f"Missing required columns: {sorted(missing)}")

    frames = {}
    for day_value, day_df in df.groupby(day_col, dropna=False):
        day_dfg = discover_direct_follows_counts_from_dataframe(
            day_df,
            case_id_col=case_id_col,
            activity_col=activity_col,
            timestamp_col=timestamp_col,
            start_label=start_label,
            end_label=end_label,
            add_start_end_nodes=True,
        )
        day_series = pd.Series(day_dfg, dtype=float, name=day_value)
        frames[day_value] = day_series

    if not frames:
        return pd.DataFrame(dtype=float)

    daily_table = pd.DataFrame(frames).fillna(0.0)
    daily_table.index = pd.MultiIndex.from_tuples(daily_table.index, names=["Source", "Target"])
    daily_table = daily_table.sort_index(axis=1)
    return daily_table


def compute_average_edge_count_per_day(
    df: pd.DataFrame,
    day_col: str,
    case_id_col: str = "CaseID",
    activity_col: str = "Activity-level 2",
    timestamp_col: str = "StartTime",
    start_label: str = "Start",
    end_label: str = "End",
) -> pd.Series:
    """
    Compute the baseline average transition count per day for each edge.
    """
    daily_table = compute_daily_edge_count_table(
        df=df,
        day_col=day_col,
        case_id_col=case_id_col,
        activity_col=activity_col,
        timestamp_col=timestamp_col,
        start_label=start_label,
        end_label=end_label,
    )
    if daily_table.empty:
        return pd.Series(dtype=float)

    baseline = daily_table.mean(axis=1).sort_values(ascending=False)
    baseline.name = "avg_transition_count_per_day"
    return baseline


def merge_dfgs_max(*dfgs: Mapping[Tuple[str, str], float]) -> DFGType:
    """
    Merge multiple DFGs by taking the maximum weight found for each edge.
    """
    merged: DFGType = {}
    for dfg in dfgs:
        for edge, weight in dfg.items():
            edge = (str(edge[0]), str(edge[1]))
            merged[edge] = max(float(weight), float(merged.get(edge, 0.0)))
    return merged


def filter_dfg_edges(
    dfg: Mapping[Tuple[str, str], float],
    threshold: int = 3,
    keep_self_loops: bool = True,
) -> DFGType:
    """
    Keep the top edges around each node based on frequency.

    For each node, keep the strongest `threshold` incoming/outgoing edges.
    """
    node_edges = defaultdict(list)
    self_loops: DFGType = {}

    for (source, target), weight in dfg.items():
        source = str(source)
        target = str(target)
        weight = float(weight)

        if source == target:
            self_loops[(source, target)] = weight
        else:
            node_edges[source].append((((source, target)), weight))
            node_edges[target].append((((source, target)), weight))

    filtered_edges: DFGType = {}
    for _, edges in node_edges.items():
        unique_edges = list({edge: weight for edge, weight in sorted(edges, key=lambda x: -x[1])}.items())
        top_edges = unique_edges[:threshold]
        for edge, weight in top_edges:
            filtered_edges[edge] = weight

    if keep_self_loops:
        filtered_edges.update(self_loops)

    return filtered_edges


def compute_readable_process_layout(
    dfg,
    start_label: str = "Start",
    end_label: str = "End",
    x_gap: float = 620.0,
    y_gap: float = 240.0,
):
    """
    Deterministic layered layout for process graphs.

    - Start is fixed at the top-left.
    - End is fixed at the bottom-right.
    - Other nodes are placed left-to-right by distance from Start.
    - Inside each layer, nodes are ordered by predecessor barycenter
      to reduce crossings and improve readability.

    Returns
    -------
    dict
        {"node_name": (x, y), ...}
    """
    G = nx.DiGraph()
    for (u, v), w in dfg.items():
        G.add_edge(str(u), str(v), weight=float(w))

    if start_label not in G:
        G.add_node(start_label)
    if end_label not in G:
        G.add_node(end_label)

    # Assign layers from Start using BFS
    layer = {start_label: 0}
    q = deque([start_label])

    while q:
        node = q.popleft()
        for nbr in G.successors(node):
            if nbr == end_label:
                continue
            if nbr not in layer:
                layer[nbr] = layer[node] + 1
                q.append(nbr)

    # Put unreachable nodes into an intermediate fallback layer
    if len(layer) < len(G.nodes):
        fallback_layer = max(layer.values(), default=0) + 1
        for node in G.nodes:
            if node not in layer and node != end_label:
                layer[node] = fallback_layer

    # Force End to the last layer
    end_layer = max(layer.values(), default=0) + 1
    layer[end_label] = end_layer

    # Group nodes by layer
    layers = {}
    for node, depth in layer.items():
        layers.setdefault(depth, []).append(node)

    ordered_layers = {}
    previous_y = {}

    for depth in sorted(layers):
        current = [n for n in layers[depth] if n not in {start_label, end_label}]

        if depth == 0:
            ordered = [start_label]
        elif depth == end_layer:
            ordered = [end_label]
        else:
            def barycenter(node: str) -> float:
                preds = [p for p in G.predecessors(node) if p in previous_y]
                if preds:
                    return sum(previous_y[p] for p in preds) / len(preds)

                succs = [s for s in G.successors(node) if s in previous_y]
                if succs:
                    return sum(previous_y[s] for s in succs) / len(succs)

                return 0.0

            current.sort(key=lambda n: (barycenter(n), -G.degree(n), n))
            ordered = current

        ordered_layers[depth] = ordered

        # Fixed spacing inside each layer to avoid overlap
        if depth == 0:
            ys = [0.0]
        elif depth == end_layer:
            ys = [max([y for y in previous_y.values()], default=0.0) + y_gap]
        else:
            total_height = (len(ordered) - 1) * y_gap
            top_y = -total_height / 2.0
            ys = [top_y + i * y_gap for i in range(len(ordered))]

        for node, y in zip(ordered, ys):
            previous_y[node] = y

    positions = {}
    for depth, nodes_in_layer in ordered_layers.items():
        x = depth * x_gap
        for node in nodes_in_layer:
            positions[node] = (float(x), float(previous_y[node]))

    # Ensure End is bottom-right
    if end_label in positions:
        positions[end_label] = (
            float(end_layer * x_gap),
            max([y for _, y in positions.values()], default=0.0) + y_gap,
        )

    return positions


# ---------------------------------------------------------------------------
# Color helpers
# ---------------------------------------------------------------------------

def _clamp(value: float, lower: float, upper: float) -> float:
    return max(lower, min(upper, value))


def _rgb_to_hex(rgb: tuple[int, int, int]) -> str:
    return "#{:02x}{:02x}{:02x}".format(*rgb)


def _hex_to_rgb(hex_color: str):
    color = str(hex_color).strip().lstrip("#")
    if len(color) == 3:
        color = "".join(ch * 2 for ch in color)
    if len(color) != 6:
        return (180, 180, 180)
    try:
        return (int(color[0:2], 16), int(color[2:4], 16), int(color[4:6], 16))
    except ValueError:
        return (180, 180, 180)

# def _hex_to_rgb(hex_color: str) -> tuple[int, int, int]:
#     hex_color = str(hex_color).strip().lstrip("#")
#     if len(hex_color) != 6:
#         raise ValueError(f"Invalid hex color: {hex_color}")
#     return tuple(int(hex_color[i : i + 2], 16) for i in (0, 2, 4))


def _interpolate_rgb(
    start_rgb: tuple[int, int, int],
    end_rgb: tuple[int, int, int],
    t: float,
) -> tuple[int, int, int]:
    t = _clamp(float(t), 0.0, 1.0)
    return tuple(
        int(round(start_rgb[i] + (end_rgb[i] - start_rgb[i]) * t))
        for i in range(3)
    )


def diverging_heatmap_color(
    value: float,
    max_abs_value: float,
    negative_color: str = "#8b0000",
    zero_color: str = "#878787",
    positive_color: str = "#006400",
    near_zero_ratio: float = 0.05,
) -> str:
    """
    Map a signed value to a red-white-green heatmap color.
    """
    value = float(value)
    max_abs_value = abs(float(max_abs_value))

    if max_abs_value == 0:
        return zero_color

    if abs(value) <= max_abs_value * near_zero_ratio:
        return zero_color

    t = abs(value) / max_abs_value
    t = _clamp(t, 0.0, 1.0)

    if value > 0:
        rgb = _interpolate_rgb(_hex_to_rgb(zero_color), _hex_to_rgb(positive_color), t)
    else:
        rgb = _interpolate_rgb(_hex_to_rgb(zero_color), _hex_to_rgb(negative_color), t)

    return _rgb_to_hex(rgb)


def edge_heatmap_color(
    value: float,
    max_abs_value: float,
    negative_color: str = "#8b0000",
    zero_color: str = "#878787",
    positive_color: str = "#006400",
    near_zero_ratio: float = 0.05,
) -> str:
    """
    Map a signed edge-difference value to red-black-green.
    """
    value = float(value)
    max_abs_value = abs(float(max_abs_value))

    if max_abs_value == 0:
        return zero_color

    if abs(value) <= max_abs_value * near_zero_ratio:
        return zero_color

    t = abs(value) / max_abs_value
    t = _clamp(t, 0.0, 1.0)

    if value > 0:
        rgb = _interpolate_rgb(_hex_to_rgb(zero_color), _hex_to_rgb(positive_color), t)
    else:
        rgb = _interpolate_rgb(_hex_to_rgb(zero_color), _hex_to_rgb(negative_color), t)

    return _rgb_to_hex(rgb)

def build_node_heatmap_attributes(
    day_values: pd.Series,
    baseline_values: pd.Series,
    max_abs_difference: Optional[float] = None,
    white_border_color: str = "#000000",
    near_zero_ratio: float = 0.05,
    start_end_nodes: Optional[Iterable[str]] = None,
    start_end_fill_color: str = "#d9d9d9",
    negative_color: str = "#ff0000",
    zero_color: str = "#a3a3a3",
    positive_color: str = "#006400",
) -> Dict[str, Dict[str, Any]]:
    """
    Build node attributes for a differential heatmap.

    Although the parameter names still say day_values / baseline_values,
    this function can be used for any comparison:
        comparison_series - baseline_series

    Example for your new use case:
        group_duration_baseline - overall_duration_baseline
    """
    start_end_nodes = set(start_end_nodes or [])
    all_nodes = sorted(set(map(str, day_values.index)) | set(map(str, baseline_values.index)) | start_end_nodes)
    aligned_day = pd.to_numeric(day_values, errors="coerce").reindex(all_nodes).fillna(0.0)
    aligned_baseline = pd.to_numeric(baseline_values, errors="coerce").reindex(all_nodes).fillna(0.0)
    diff = aligned_day - aligned_baseline

    if max_abs_difference is None:
        max_abs_difference = float(diff.abs().max()) if len(diff) else 0.0

    node_attributes: Dict[str, Dict[str, Any]] = {}
    for node in all_nodes:
        baseline_value = float(aligned_baseline.loc[node])
        comparison_value = float(aligned_day.loc[node])
        diff_value = float(diff.loc[node])
        diff_ratio = diff_value / baseline_value if baseline_value not in {0.0, -0.0} else None

        if node in start_end_nodes:
            fill_color = start_end_fill_color
            border_color = white_border_color
        else:
            fill_color = diverging_heatmap_color(
                value=diff_value,
                max_abs_value=max_abs_difference,
                negative_color=negative_color,
                zero_color=zero_color,
                positive_color=positive_color,
                near_zero_ratio=near_zero_ratio,
            )
            border_color = white_border_color if fill_color.lower() == zero_color.lower() else fill_color

        node_attributes[node] = {
            "fill_color": fill_color,
            "border_color": border_color,
            "border_width": 1.5 if fill_color.lower() == zero_color.lower() else 1.0,
            "baseline_value": baseline_value,
            "day_value": comparison_value,
            "difference_value": diff_value,
            "difference_ratio": diff_ratio,
        }

    return node_attributes


def build_edge_heatmap_attributes(
    day_values: pd.Series,
    baseline_values: pd.Series,
    max_abs_difference: Optional[float] = None,
    near_zero_ratio: float = 0.05,
    negative_color: str = "#ff0000",
    zero_color: str = "#808080",
    positive_color: str = "#006400",
) -> Dict[Tuple[str, str], Dict[str, Any]]:
    """
    Build edge attributes for a differential heatmap.

    This is suitable for:
        group_edge_baseline - overall_edge_baseline
    """
    all_edges = sorted(set(baseline_values.index) | set(day_values.index))
    aligned_day = pd.to_numeric(day_values, errors="coerce").reindex(all_edges).fillna(0.0)
    aligned_baseline = pd.to_numeric(baseline_values, errors="coerce").reindex(all_edges).fillna(0.0)
    diff = aligned_day - aligned_baseline

    if max_abs_difference is None:
        max_abs_difference = float(diff.abs().max()) if len(diff) else 0.0

    edge_attributes: Dict[Tuple[str, str], Dict[str, Any]] = {}
    for edge in all_edges:
        edge_tuple = (str(edge[0]), str(edge[1]))
        baseline_value = float(aligned_baseline.loc[edge])
        comparison_value = float(aligned_day.loc[edge])
        diff_value = float(diff.loc[edge])
        diff_ratio = diff_value / baseline_value if baseline_value not in {0.0, -0.0} else None

        color = edge_heatmap_color(
            value=diff_value,
            max_abs_value=max_abs_difference,
            negative_color=negative_color,
            zero_color=zero_color,
            positive_color=positive_color,
            near_zero_ratio=near_zero_ratio,
        )

        edge_attributes[edge_tuple] = {
            "edge_color": color,
            "baseline_value": baseline_value,
            "day_value": comparison_value,
            "difference_value": diff_value,
            "difference_ratio": diff_ratio,
        }

    return edge_attributes

def build_differential_edge_weights(
    comparison_values: pd.Series,
    baseline_values: pd.Series,
    min_weight: float = 0.001,
) -> Dict[Tuple[str, str], float]:
    """
    Build edge weights for differential graphs.

    Thickness represents |comparison - baseline|.
    Color represents the sign of the difference.
    """
    all_edges = sorted(set(baseline_values.index) | set(comparison_values.index))
    aligned_comp = pd.to_numeric(comparison_values, errors="coerce").reindex(all_edges).fillna(0.0)
    aligned_base = pd.to_numeric(baseline_values, errors="coerce").reindex(all_edges).fillna(0.0)
    diff_abs = (aligned_comp - aligned_base).abs()

    return {
        (str(edge[0]), str(edge[1])): max(float(diff_abs.loc[edge]), float(min_weight))
        for edge in all_edges
    }

def sequential_heatmap_color(
    value: float,
    min_value: float = 1.0,
    max_value: float = 1.0,
    min_color: str = "#8b0000",
    max_color: str = "#006400",
) -> str:
    """
    Map a non-negative value to a red -> green heatmap.
    Minimum is dark red, maximum is dark green.
    """
    value = float(value)
    min_value = float(min_value)
    max_value = float(max_value)

    if max_value <= min_value:
        return max_color

    value = _clamp(value, min_value, max_value)
    t = (value - min_value) / (max_value - min_value)
    rgb = _interpolate_rgb(_hex_to_rgb(min_color), _hex_to_rgb(max_color), t)
    return _rgb_to_hex(rgb)


def build_simple_node_heatmap_attributes(
    values: pd.Series,
    min_value: float = 1.0,
    max_value: Optional[float] = None,
    start_end_nodes: Optional[Iterable[str]] = None,
    start_end_fill_color: str = "#d9d9d9",
    default_border_color: str = "#000000",
) -> Dict[str, Dict[str, Any]]:
    """
    Build node attributes for a simple (non-differential) heatmap graph.
    Values are typically the group's average duration per day for each activity.
    """
    start_end_nodes = set(start_end_nodes or [])
    all_nodes = sorted(set(map(str, values.index)) | start_end_nodes)

    aligned_values = pd.to_numeric(values, errors="coerce").reindex(all_nodes).fillna(0.0)

    if max_value is None:
        nonzero = aligned_values[aligned_values > 0]
        max_value = float(nonzero.max()) if len(nonzero) else float(min_value)

    node_attributes: Dict[str, Dict[str, Any]] = {}
    for node in all_nodes:
        metric_value = float(aligned_values.loc[node])

        if node in start_end_nodes:
            fill_color = start_end_fill_color
            border_color = default_border_color
        else:
            color_value = max(metric_value, float(min_value))
            fill_color = sequential_heatmap_color(
                value=color_value,
                min_value=min_value,
                max_value=max_value,
            )
            border_color = fill_color

        node_attributes[node] = {
            "fill_color": fill_color,
            "border_color": border_color,
            "border_width": 1.0,
            "baseline_value": metric_value,
            "heatmap_value": metric_value,
            "heatmap_mode": "simple_duration",
        }

    return node_attributes


def build_simple_edge_attributes(
    edge_values: pd.Series,
    default_color: str = "#000000",
) -> Dict[Tuple[str, str], Dict[str, Any]]:
    """
    Build edge attributes for a simple (non-differential) group graph.
    Edge color stays neutral; edge weight/thickness carries the meaning.
    """
    if edge_values is None or len(edge_values) == 0:
        return {}

    aligned = pd.to_numeric(edge_values, errors="coerce").fillna(0.0)

    edge_attributes: Dict[Tuple[str, str], Dict[str, Any]] = {}
    for edge, value in aligned.items():
        edge_tuple = (str(edge[0]), str(edge[1]))
        edge_attributes[edge_tuple] = {
            "edge_color": default_color,
            "baseline_value": float(value),
            "heatmap_value": float(value),
            "heatmap_mode": "simple_edge_weight",
        }

    return edge_attributes


# ---------------------------------------------------------------------------
# Graph building / exporting
# ---------------------------------------------------------------------------

def build_networkx_graph(
    dfg,
    node_sizes=None,
    activity_colors=None,
    node_labels=None,
    edge_weights=None,
    node_attributes=None,
    edge_attributes=None,
    node_positions=None,
    include_viz: bool = False,
):
    """
    Create a NetworkX DiGraph from a DFG dictionary.

    - heatmap colors are kept in node_attributes / edge_attributes
    - deterministic node positions from node_positions
    - GraphML-safe scalar attributes for export

    If include_viz=True, GEXF export carries positions/colors/sizes into Gephi.
    """
    node_sizes = node_sizes or {}
    activity_colors = activity_colors or {}
    node_labels = node_labels or {}
    node_attributes = node_attributes or {}
    edge_attributes = edge_attributes or {}
    node_positions = node_positions or {}

    graph = nx.DiGraph()

    all_nodes = sorted({str(node) for edge in dfg.keys() for node in edge})
    for node in all_nodes:
        raw_node_attrs = dict(node_attributes.get(node, {}))
        clean_node_attrs = _clean_graph_attrs_for_graphml(raw_node_attrs)

        render_color = str(clean_node_attrs.get("fill_color", activity_colors.get(node, "#bdbdbd")))

        attrs = {
            "size": float(node_sizes.get(node, 20.0)),
            "color": render_color,
            "label": str(node_labels.get(node, node)),
        }
        attrs.update(clean_node_attrs)

        if node in node_positions:
            x, y = node_positions[node]
            attrs["x"] = float(x)
            attrs["y"] = float(y)
            attrs["z"] = 0.0

        if include_viz:
            r, g, b = _hex_to_rgb(render_color)
            viz = {
                "size": float(node_sizes.get(node, 20.0)),
                "color": {"r": int(r), "g": int(g), "b": int(b), "a": 1.0},
            }
            if node in node_positions:
                x, y = node_positions[node]
                viz["position"] = {"x": float(x), "y": float(y), "z": 0.0}
            attrs["viz"] = viz

        graph.add_node(node, **attrs)

    for (source, target), weight in dfg.items():
        source = str(source)
        target = str(target)
        final_weight = float(weight if edge_weights is None else edge_weights.get((source, target), weight))

        raw_edge_attrs = dict(edge_attributes.get((source, target), {}))
        clean_edge_attrs = _clean_graph_attrs_for_graphml(raw_edge_attrs)
        render_color = str(clean_edge_attrs.get("edge_color", "#000000"))

        attrs = {
            "weight": final_weight,
            "color": render_color,
        }
        attrs.update(clean_edge_attrs)

        if include_viz:
            r, g, b = _hex_to_rgb(render_color)
            attrs["viz"] = {
                "color": {"r": int(r), "g": int(g), "b": int(b), "a": 1.0},
                "thickness": max(1.0, float(final_weight)),
            }

        graph.add_edge(source, target, **attrs)

    return graph


def export_to_graphml(
    dfg,
    node_sizes=None,
    activity_colors=None,
    node_labels=None,
    edge_weights=None,
    node_attributes=None,
    edge_attributes=None,
    node_positions=None,
    output_file="graph.graphml",
):
    """
    Export a DFG to GraphML.

    The GraphML contains plain scalar attributes such as:
    - color / fill_color / border_color
    - baseline_value / day_value / difference_value / difference_ratio
    - x / y / z positions
    - edge_color
    """
    output_file = Path(output_file)
    output_file.parent.mkdir(parents=True, exist_ok=True)

    graph = build_networkx_graph(
        dfg=dfg,
        node_sizes=node_sizes,
        activity_colors=activity_colors,
        node_labels=node_labels,
        edge_weights=edge_weights,
        node_attributes=node_attributes,
        edge_attributes=edge_attributes,
        node_positions=node_positions,
        include_viz=False,
    )
    nx.write_graphml(graph, output_file)
    return output_file

def export_to_gexf(
    dfg,
    node_sizes=None,
    activity_colors=None,
    node_labels=None,
    edge_weights=None,
    node_attributes=None,
    edge_attributes=None,
    node_positions=None,
    output_file="graph.gexf",
):
    """
    Export a DFG to GEXF with Gephi-readable viz metadata.

    Node and edge colors come from the heatmap attributes when available.
    Node positions come from node_positions.
    """
    output_file = Path(output_file)
    output_file.parent.mkdir(parents=True, exist_ok=True)

    graph = build_networkx_graph(
        dfg=dfg,
        node_sizes=node_sizes,
        activity_colors=activity_colors,
        node_labels=node_labels,
        edge_weights=edge_weights,
        node_attributes=node_attributes,
        edge_attributes=edge_attributes,
        node_positions=node_positions,
        include_viz=True,
    )
    nx.write_gexf(graph, output_file)
    return output_file


def export_to_csv(
    dfg,
    node_sizes=None,
    activity_colors=None,
    node_labels=None,
    edge_weights=None,
    node_attributes=None,
    edge_attributes=None,
    nodes_file="nodes.csv",
    edges_file="edges.csv",
):
    """
    Export nodes and edges to CSV files with the same visible colors used in export.
    """
    node_sizes = node_sizes or {}
    activity_colors = activity_colors or {}
    node_labels = node_labels or {}
    edge_weights = edge_weights or {}
    node_attributes = node_attributes or {}
    edge_attributes = edge_attributes or {}

    nodes_file = Path(nodes_file)
    edges_file = Path(edges_file)
    nodes_file.parent.mkdir(parents=True, exist_ok=True)
    edges_file.parent.mkdir(parents=True, exist_ok=True)

    all_nodes = sorted({str(node) for edge in dfg.keys() for node in edge})
    node_rows = []
    for node in all_nodes:
        raw_node_attrs = dict(node_attributes.get(node, {}))
        clean_node_attrs = _clean_graph_attrs_for_graphml(raw_node_attrs)
        render_color = str(clean_node_attrs.get("fill_color", activity_colors.get(node, "#808080")))

        row = {
            "Id": node,
            "Label": str(node_labels.get(node, node)),
            "Size": float(node_sizes.get(node, 20.0)),
            "Color": render_color,
        }
        row.update(clean_node_attrs)
        node_rows.append(row)
    nodes_df = pd.DataFrame(node_rows)

    edge_rows = []
    for (source, target), weight in dfg.items():
        source = str(source)
        target = str(target)
        raw_edge_attrs = dict(edge_attributes.get((source, target), {}))
        clean_edge_attrs = _clean_graph_attrs_for_graphml(raw_edge_attrs)
        render_color = str(clean_edge_attrs.get("edge_color", "#000000"))

        row = {
            "Source": source,
            "Target": target,
            "Weight": float(edge_weights.get((source, target), weight)),
            "Color": render_color,
        }
        row.update(clean_edge_attrs)
        edge_rows.append(row)
    edges_df = pd.DataFrame(edge_rows)

    nodes_df.to_csv(nodes_file, index=False)
    edges_df.to_csv(edges_file, index=False)

    return nodes_file, edges_file


def edge_series_to_export_table(
    group_name: str,
    baseline_series: pd.Series,
    difference_table: pd.DataFrame,
    baseline_column_name: str = "Baseline_transition_count",
    day_prefix: str = "diff__",
) -> pd.DataFrame:
    """
    Build a wide edge-difference export table for one group.
    """
    baseline_df = baseline_series.rename(baseline_column_name).to_frame()
    export_df = baseline_df.join(difference_table, how="outer").fillna(0.0).reset_index()
    export_df.columns = ["Source", "Target"] + list(export_df.columns[2:])
    export_df.insert(0, "Group", group_name)

    rename_map = {
        col: f"{day_prefix}{col}"
        for col in export_df.columns
        if col not in {"Group", "Source", "Target", baseline_column_name}
    }
    export_df = export_df.rename(columns=rename_map)
    return export_df
