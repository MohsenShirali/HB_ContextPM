# ContextPM - Contextualized Process Mining
# Copyright (C) 2026 Mohsen Shirali and Zahra Ahmadi (LIRIS, KU Leuven)
#
# This program is free software: you can redistribute it and/or modify it
# under the terms of the GNU General Public License version 3 as published by
# the Free Software Foundation. It is distributed WITHOUT ANY WARRANTY; see the
# LICENSE file or <https://www.gnu.org/licenses/gpl-3.0.html> for details.
"""
Helpers for listing and previewing the files produced by a run.

Everything a run produces lives below ``<outputs_dir>/<dataset_name>/``.  The
dashboard shows that folder as a browsable list; this module classifies each
file so the UI can offer the right viewer (table, image, graph viewer, ...).
"""
from __future__ import annotations

import datetime as dt
import json
from pathlib import Path
from typing import Any, Dict, List

import pandas as pd

TYPE_BY_EXTENSION = {
    ".csv": "csv",
    ".png": "image",
    ".jpg": "image",
    ".jpeg": "image",
    ".svg": "image",
    ".graphml": "graphml",
    ".gexf": "gexf",
    ".json": "json",
    ".txt": "text",
    ".log": "text",
    ".xlsx": "excel",
}

BUNDLE_MARKER = "contextpm_graph"


def classify(relative_path: str, extension: str) -> Dict[str, str]:
    """Derive a coarse category / group / kind from the location of a file."""
    parts = relative_path.split("/")
    name = parts[-1].lower()
    folders = [p.lower() for p in parts[:-1]]
    group = parts[0] if len(parts) > 1 else ""

    if "figures" in folders:
        category = "figure"
    elif extension in {".graphml", ".gexf"}:
        category = "graph"
    elif extension == ".json":
        category = "viewer"
    elif extension == ".csv":
        category = "table"
    elif extension in {".png", ".jpg", ".jpeg", ".svg"}:
        category = "image"
    else:
        category = "other"

    if "days" in folders:
        kind = "day"
    elif "differential_vs_overall" in folders:
        kind = "differential"
    elif "simple_group_graph" in folders:
        kind = "simple"
    elif "overall_data" in folders:
        kind = "baseline"
    elif "figures" in folders:
        kind = "figure"
    else:
        kind = "kpi" if category == "table" else "other"

    return {"category": category, "group": group, "kind": kind}


def scan_outputs(root: Path) -> List[Dict[str, Any]]:
    """List every file below ``root`` with type information for the UI."""
    root = Path(root)
    if not root.exists():
        return []
    entries: List[Dict[str, Any]] = []
    for path in sorted(root.rglob("*")):
        if path.is_dir() or path.name.startswith("."):
            continue
        rel = path.relative_to(root).as_posix()
        ext = path.suffix.lower()
        stat = path.stat()
        info = classify(rel, ext)
        if ext == ".json" and info["category"] == "viewer" and not _is_bundle(path):
            info["category"] = "other"
        entries.append(
            {
                "path": rel,
                "name": path.name,
                "folder": path.parent.relative_to(root).as_posix() if path.parent != root else "",
                "ext": ext,
                "type": TYPE_BY_EXTENSION.get(ext, "other"),
                "size": stat.st_size,
                "modified": dt.datetime.fromtimestamp(stat.st_mtime).isoformat(timespec="seconds"),
                **info,
            }
        )
    return entries


def _is_bundle(path: Path) -> bool:
    try:
        with open(path, "r", encoding="utf-8") as handle:
            head = handle.read(200)
        return BUNDLE_MARKER in head
    except OSError:
        return False


def list_dataset_folders(outputs_dir: Path) -> List[str]:
    outputs_dir = Path(outputs_dir)
    if not outputs_dir.exists():
        return []
    return sorted(p.name for p in outputs_dir.iterdir() if p.is_dir() and not p.name.startswith("."))


def csv_preview(path: Path, max_rows: int = 300) -> Dict[str, Any]:
    """Return the first rows of a CSV file as JSON-friendly lists."""
    frame = pd.read_csv(path, nrows=max_rows)
    with open(path, "r", encoding="utf-8", errors="replace") as handle:
        total_rows = max(sum(1 for _ in handle) - 1, 0)
    rows = []
    for record in frame.itertuples(index=False):
        rows.append(["" if _is_missing(v) else _fmt(v) for v in record])
    return {
        "columns": [str(c) for c in frame.columns],
        "rows": rows,
        "shown_rows": len(rows),
        "total_rows": total_rows,
    }


def text_preview(path: Path, max_chars: int = 20000) -> Dict[str, Any]:
    with open(path, "r", encoding="utf-8", errors="replace") as handle:
        text = handle.read(max_chars + 1)
    truncated = len(text) > max_chars
    if path.suffix.lower() == ".json" and not truncated:
        try:
            text = json.dumps(json.loads(text), indent=2)[:max_chars]
        except ValueError:
            pass
    return {"text": text[:max_chars], "truncated": truncated}


def _is_missing(value: Any) -> bool:
    try:
        return bool(pd.isna(value))
    except (TypeError, ValueError):
        return False


def _fmt(value: Any) -> str:
    if isinstance(value, float):
        return f"{value:.6g}"
    return str(value)
