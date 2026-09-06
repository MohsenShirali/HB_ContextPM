# ContextPM - Contextualized Process Mining
# Copyright (C) 2026 Mohsen Shirali and Zahra Ahmadi (LIRIS, KU Leuven)
#
# This program is free software: you can redistribute it and/or modify it
# under the terms of the GNU General Public License version 3 as published by
# the Free Software Foundation. It is distributed WITHOUT ANY WARRANTY; see the
# LICENSE file or <https://www.gnu.org/licenses/gpl-3.0.html> for details.
"""
Graph "bundles" for the interactive viewer.

Next to every exported GraphML/GEXF file the pipeline writes a small JSON
bundle that contains the same nodes, edges, positions, colours and sizes plus
the legend information the viewer needs.  The bundles are what the browser
loads; the GraphML/GEXF files stay Gephi-compatible.
"""
from __future__ import annotations

import copy
import json
import logging
import math
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, Optional, Tuple

import networkx as nx

from PM_in_Graph import _clean_graph_attrs_for_graphml, export_to_gexf, export_to_graphml

log = logging.getLogger("contextpm.viewer")

BUNDLE_MARKER = "contextpm_graph"
NODE_STYLE_KEYS = {"fill_color", "border_color", "border_width"}
EDGE_STYLE_KEYS = {"edge_color"}
KIND_ORDER = {"baseline": 0, "simple": 1, "differential": 2, "day": 3, "custom": 4}
KIND_TITLES = {
    "baseline": "Baseline (overall data)",
    "simple": "Group maps",
    "differential": "Differential maps (group vs. overall)",
    "day": "Day maps (day vs. group baseline)",
    "custom": "Other graph files",
}
LOCKED_KINDS = {"differential", "day"}


def _num(value: Any, default: float = 0.0) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return default
    if math.isnan(number) or math.isinf(number):
        return default
    return number


def _jsonable(value: Any) -> Any:
    if isinstance(value, (str, bool)) or value is None:
        return value
    if isinstance(value, (int, float)):
        if isinstance(value, float) and (math.isnan(value) or math.isinf(value)):
            return None
        return value
    return str(value)


def build_bundle(
    *,
    graph_id: str,
    name: str,
    kind: str,
    dfg: Mapping[Tuple[str, str], float],
    node_sizes: Optional[Mapping[str, float]] = None,
    node_labels: Optional[Mapping[str, str]] = None,
    edge_weights: Optional[Mapping[Tuple[str, str], float]] = None,
    node_attributes: Optional[Mapping[str, Mapping[str, Any]]] = None,
    edge_attributes: Optional[Mapping[Tuple[str, str], Mapping[str, Any]]] = None,
    node_positions: Optional[Mapping[str, Tuple[float, float]]] = None,
    legend: Optional[Dict[str, Any]] = None,
    files: Optional[Dict[str, str]] = None,
    base_labels: Optional[Mapping[str, str]] = None,
    group: Optional[str] = None,
    day: Optional[str] = None,
    description: str = "",
    locked_colors: Optional[bool] = None,
) -> Dict[str, Any]:
    """Create the viewer bundle from the same inputs used for the GraphML export."""
    node_sizes = node_sizes or {}
    node_labels = node_labels or {}
    node_attributes = node_attributes or {}
    edge_attributes = edge_attributes or {}
    node_positions = node_positions or {}
    base_labels = base_labels or {}

    nodes: List[Dict[str, Any]] = []
    for node in sorted({str(n) for edge in dfg.keys() for n in edge}):
        attrs = _clean_graph_attrs_for_graphml(dict(node_attributes.get(node, {})))
        x, y = node_positions.get(node, (0.0, 0.0))
        nodes.append(
            {
                "id": node,
                "label": str(node_labels.get(node, node)),
                "base_label": str(base_labels.get(node, node)),
                "size": _num(node_sizes.get(node, 20.0), 20.0),
                "color": str(attrs.get("fill_color", "#bdbdbd")),
                "border_color": str(attrs.get("border_color", "#ffffff")),
                "border_width": _num(attrs.get("border_width", 1.0), 1.0),
                "x": _num(x),
                "y": _num(y),
                "metrics": {k: _jsonable(v) for k, v in attrs.items() if k not in NODE_STYLE_KEYS},
            }
        )

    edges: List[Dict[str, Any]] = []
    for (source, target), frequency in dfg.items():
        source, target = str(source), str(target)
        attrs = _clean_graph_attrs_for_graphml(dict(edge_attributes.get((source, target), {})))
        weight = frequency if edge_weights is None else edge_weights.get((source, target), frequency)
        edges.append(
            {
                "id": f"{source}->{target}",
                "source": source,
                "target": target,
                "weight": _num(weight),
                "frequency": _num(frequency),
                "color": str(attrs.get("edge_color", "#000000")),
                "metrics": {k: _jsonable(v) for k, v in attrs.items() if k not in EDGE_STYLE_KEYS},
            }
        )

    if locked_colors is None:
        locked_colors = kind in LOCKED_KINDS

    return {
        BUNDLE_MARKER: True,
        "version": 1,
        "id": graph_id,
        "name": name,
        "kind": kind,
        "group": group,
        "day": day,
        "description": description,
        "locked_colors": bool(locked_colors),
        "edited": False,
        "files": dict(files or {}),
        "legend": legend or {},
        "nodes": nodes,
        "edges": edges,
        "counts": {"nodes": len(nodes), "edges": len(edges)},
    }


def write_bundle(bundle: Dict[str, Any], path: Path) -> Path:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as handle:
        json.dump(bundle, handle, indent=1, default=str)
    return path


def load_bundle(path: Path) -> Dict[str, Any]:
    with open(path, "r", encoding="utf-8") as handle:
        bundle = json.load(handle)
    if not isinstance(bundle, dict) or not bundle.get(BUNDLE_MARKER):
        raise ValueError(f"{path} is not a ContextPM graph bundle")
    return bundle


def summarize(bundle: Dict[str, Any], rel_path: Optional[str] = None) -> Dict[str, Any]:
    return {
        "id": bundle["id"],
        "name": bundle.get("name", bundle["id"]),
        "kind": bundle.get("kind", "custom"),
        "kind_title": KIND_TITLES.get(bundle.get("kind", "custom"), "Other"),
        "group": bundle.get("group"),
        "day": bundle.get("day"),
        "locked_colors": bool(bundle.get("locked_colors")),
        "edited": bool(bundle.get("edited")),
        "counts": bundle.get("counts", {}),
        "files": bundle.get("files", {}),
        "path": rel_path or bundle.get("files", {}).get("json"),
    }


def list_bundles(root: Path) -> List[Dict[str, Any]]:
    """Find every bundle below ``root`` (sorted: baseline, groups, differential, days)."""
    root = Path(root)
    if not root.exists():
        return []
    items: List[Dict[str, Any]] = []
    for path in root.rglob("*.json"):
        try:
            with open(path, "r", encoding="utf-8") as handle:
                if BUNDLE_MARKER not in handle.read(200):
                    continue
            bundle = load_bundle(path)
        except (OSError, ValueError, json.JSONDecodeError):
            continue
        items.append(summarize(bundle, path.relative_to(root).as_posix()))
    items.sort(key=lambda s: (KIND_ORDER.get(s["kind"], 9), str(s.get("group") or ""), str(s.get("day") or ""), s["name"]))
    return items


def find_bundle(root: Path, graph_id: str) -> Tuple[Dict[str, Any], Path]:
    """Locate a bundle by its id (the relative file stem)."""
    root = Path(root)
    candidate = root / (graph_id + ".json")
    if candidate.exists():
        return load_bundle(candidate), candidate
    for summary in list_bundles(root):
        if summary["id"] == graph_id and summary.get("path"):
            path = root / summary["path"]
            return load_bundle(path), path
    raise FileNotFoundError(f"Graph '{graph_id}' was not found below {root}")


# ---------------------------------------------------------------------------
# Editing (positions / colours changed in the interactive viewer)
# ---------------------------------------------------------------------------

def apply_edits(bundle: Dict[str, Any], edits: Mapping[str, Any]) -> Dict[str, Any]:
    """Return a copy of ``bundle`` with the viewer edits applied.

    ``edits`` may contain ``positions`` ({node id: {x, y}}), ``node_colors``,
    ``node_labels`` ({node id: value}), ``edge_colors`` and ``edge_weights``
    ({edge id: value}).  Colour edits are ignored for graphs whose colours are
    derived from differential values (``locked_colors``).
    """
    edited = copy.deepcopy(bundle)
    locked = bool(edited.get("locked_colors"))
    positions = edits.get("positions") or {}
    node_colors = {} if locked else (edits.get("node_colors") or {})
    node_labels = edits.get("node_labels") or {}
    edge_colors = {} if locked else (edits.get("edge_colors") or {})
    edge_weights = edits.get("edge_weights") or {}

    for node in edited["nodes"]:
        pos = positions.get(node["id"])
        if isinstance(pos, dict):
            node["x"] = _num(pos.get("x"), node["x"])
            node["y"] = _num(pos.get("y"), node["y"])
        if node["id"] in node_colors and node_colors[node["id"]]:
            node["color"] = str(node_colors[node["id"]])
            node["border_color"] = str(node_colors[node["id"]])
        if node["id"] in node_labels and node_labels[node["id"]] is not None:
            node["label"] = str(node_labels[node["id"]])

    for edge in edited["edges"]:
        if edge["id"] in edge_colors and edge_colors[edge["id"]]:
            edge["color"] = str(edge_colors[edge["id"]])
        if edge["id"] in edge_weights:
            edge["weight"] = _num(edge_weights[edge["id"]], edge["weight"])

    if not edited.get("edited"):
        edited["id"] = edited["id"] + "__edited"
        edited["name"] = edited.get("name", edited["id"]) + " (edited)"
    edited["edited"] = True
    edited["counts"] = {"nodes": len(edited["nodes"]), "edges": len(edited["edges"])}
    return edited


def export_bundle(bundle: Dict[str, Any], root: Path, stem: Path, export_gexf: bool = True) -> Dict[str, str]:
    """Write GraphML (+GEXF) and the JSON bundle for ``bundle`` at ``stem``."""
    root = Path(root)
    stem = Path(stem)
    stem.parent.mkdir(parents=True, exist_ok=True)

    dfg = {(e["source"], e["target"]): _num(e.get("frequency", e["weight"]), 1.0) for e in bundle["edges"]}
    node_sizes = {n["id"]: _num(n["size"], 20.0) for n in bundle["nodes"]}
    node_labels = {n["id"]: n["label"] for n in bundle["nodes"]}
    edge_weights = {(e["source"], e["target"]): _num(e["weight"]) for e in bundle["edges"]}
    node_attributes = {
        n["id"]: {
            "fill_color": n["color"],
            "border_color": n.get("border_color", "#ffffff"),
            "border_width": _num(n.get("border_width", 1.0), 1.0),
            **{k: v for k, v in (n.get("metrics") or {}).items() if v is not None},
        }
        for n in bundle["nodes"]
    }
    edge_attributes = {
        (e["source"], e["target"]): {
            "edge_color": e["color"],
            **{k: v for k, v in (e.get("metrics") or {}).items() if v is not None},
        }
        for e in bundle["edges"]
    }
    positions = {n["id"]: (_num(n["x"]), _num(n["y"])) for n in bundle["nodes"]}

    files: Dict[str, str] = {}
    graphml_path = stem.parent / (stem.name + ".graphml")
    export_to_graphml(
        dfg=dfg,
        node_sizes=node_sizes,
        node_labels=node_labels,
        edge_weights=edge_weights,
        node_attributes=node_attributes,
        edge_attributes=edge_attributes,
        node_positions=positions,
        output_file=graphml_path,
    )
    files["graphml"] = graphml_path.relative_to(root).as_posix()
    if export_gexf:
        gexf_path = stem.parent / (stem.name + ".gexf")
        export_to_gexf(
            dfg=dfg,
            node_sizes=node_sizes,
            node_labels=node_labels,
            edge_weights=edge_weights,
            node_attributes=node_attributes,
            edge_attributes=edge_attributes,
            node_positions=positions,
            output_file=gexf_path,
        )
        files["gexf"] = gexf_path.relative_to(root).as_posix()
    json_path = stem.parent / (stem.name + ".json")
    files["json"] = json_path.relative_to(root).as_posix()
    bundle["files"] = files
    write_bundle(bundle, json_path)
    return files


# ---------------------------------------------------------------------------
# Fallback: open any GraphML / GEXF file that has no bundle next to it
# ---------------------------------------------------------------------------

def bundle_from_graph_file(path: Path, root: Path) -> Dict[str, Any]:
    """Build a bundle from a GraphML/GEXF file (e.g. produced by the notebooks)."""
    path = Path(path)
    if path.suffix.lower() == ".graphml":
        graph = nx.read_graphml(path)
    elif path.suffix.lower() == ".gexf":
        graph = nx.read_gexf(path)
    else:
        raise ValueError("Only .graphml and .gexf files can be opened")

    rel = path.relative_to(root)
    folders = {p.lower() for p in rel.parts[:-1]}
    if "days" in folders:
        kind = "day"
    elif "differential_vs_overall" in folders:
        kind = "differential"
    elif "simple_group_graph" in folders:
        kind = "simple"
    elif "overall_data" in folders:
        kind = "baseline"
    else:
        kind = "custom"

    dfg: Dict[Tuple[str, str], float] = {}
    edge_weights: Dict[Tuple[str, str], float] = {}
    edge_attributes: Dict[Tuple[str, str], Dict[str, Any]] = {}
    for source, target, data in graph.edges(data=True):
        key = (str(source), str(target))
        weight = _num(data.get("weight", 1.0), 1.0)
        dfg[key] = weight
        edge_weights[key] = weight
        viz = data.get("viz") or {}
        color = data.get("edge_color") or data.get("color") or _viz_color(viz) or "#000000"
        attrs = {k: v for k, v in data.items() if k not in {"viz", "weight", "color", "id"}}
        attrs["edge_color"] = color
        edge_attributes[key] = attrs

    node_sizes: Dict[str, float] = {}
    node_labels: Dict[str, str] = {}
    node_attributes: Dict[str, Dict[str, Any]] = {}
    positions: Dict[str, Tuple[float, float]] = {}
    has_difference = False
    for node, data in graph.nodes(data=True):
        node = str(node)
        viz = data.get("viz") or {}
        node_sizes[node] = _num(data.get("size", viz.get("size", 20.0)), 20.0)
        node_labels[node] = str(data.get("label", node))
        pos = viz.get("position") or {}
        positions[node] = (_num(data.get("x", pos.get("x", 0.0))), _num(data.get("y", pos.get("y", 0.0))))
        attrs = {k: v for k, v in data.items() if k not in {"viz", "size", "label", "x", "y", "z", "color"}}
        attrs["fill_color"] = data.get("fill_color") or data.get("color") or _viz_color(viz) or "#bdbdbd"
        attrs.setdefault("border_color", "#ffffff")
        attrs.setdefault("border_width", 1.0)
        if "difference_value" in data:
            has_difference = True
        node_attributes[node] = attrs
        if node not in {n for e in dfg for n in e}:
            dfg.setdefault((node, node), 0.0)  # isolated node: keep it visible

    if kind == "custom" and has_difference:
        kind = "differential"

    legend = _legend_from_attributes(kind, node_attributes, edge_attributes, edge_weights, node_sizes)
    files = {path.suffix.lower().lstrip("."): rel.as_posix()}
    return build_bundle(
        graph_id=rel.with_suffix("").as_posix(),
        name=rel.with_suffix("").name,
        kind=kind,
        dfg=dfg,
        node_sizes=node_sizes,
        node_labels=node_labels,
        edge_weights=edge_weights,
        node_attributes=node_attributes,
        edge_attributes=edge_attributes,
        node_positions=positions,
        legend=legend,
        files=files,
        description=f"Loaded from {rel.as_posix()}",
        locked_colors=kind in LOCKED_KINDS or has_difference,
    )


def _viz_color(viz: Mapping[str, Any]) -> Optional[str]:
    color = viz.get("color") if isinstance(viz, Mapping) else None
    if not isinstance(color, Mapping):
        return None
    try:
        return "#{:02x}{:02x}{:02x}".format(int(color.get("r", 0)), int(color.get("g", 0)), int(color.get("b", 0)))
    except (TypeError, ValueError):
        return None


def _legend_from_attributes(kind, node_attributes, edge_attributes, edge_weights, node_sizes) -> Dict[str, Any]:
    legend: Dict[str, Any] = {}
    weights = [w for w in edge_weights.values() if w > 0]
    if kind in LOCKED_KINDS:
        node_max = max((abs(_num(a.get("difference_value"))) for a in node_attributes.values()), default=0.0)
        edge_max = max((abs(_num(a.get("difference_value"))) for a in edge_attributes.values()), default=0.0)
        legend["node_color"] = {
            "type": "diverging",
            "title": "Node colour = duration difference vs. baseline",
            "min": -node_max / 60.0,
            "max": node_max / 60.0,
            "unit": "min",
            "colors": ["#ff0000", "#a3a3a3", "#006400"],
        }
        legend["edge_color"] = {
            "type": "diverging",
            "title": "Edge colour = transition difference vs. baseline",
            "min": -edge_max,
            "max": edge_max,
            "unit": "/day",
            "colors": ["#ff0000", "#a3a3a3", "#006400"],
        }
        legend["edge_width"] = {"type": "range", "title": "Edge thickness = |difference|", "min": 0.0, "max": max(weights, default=1.0), "unit": "", "format": "number"}
    else:
        by_color: Dict[str, List[str]] = {}
        for node, attrs in node_attributes.items():
            by_color.setdefault(str(attrs.get("fill_color", "#bdbdbd")).lower(), []).append(node)
        legend["node_color"] = {
            "type": "categorical",
            "title": "Node colour",
            "entries": [{"color": c, "label": ", ".join(sorted(v))} for c, v in by_color.items()],
        }
        legend["edge_width"] = {"type": "range", "title": "Edge thickness = weight", "min": min(weights, default=0.0), "max": max(weights, default=1.0), "unit": "", "format": "number"}
    sizes = [s for s in node_sizes.values()]
    legend["node_size"] = {"type": "range", "title": "Node size", "min": min(sizes, default=0.0), "max": max(sizes, default=1.0), "unit": "", "format": "number"}
    return legend
