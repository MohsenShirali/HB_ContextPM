# ContextPM - Contextualized Process Mining
# Copyright (C) 2026 Mohsen Shirali and Zahra Ahmadi (LIRIS, KU Leuven)
#
# This program is free software: you can redistribute it and/or modify it
# under the terms of the GNU General Public License version 3 as published by
# the Free Software Foundation. It is distributed WITHOUT ANY WARRANTY; see the
# LICENSE file or <https://www.gnu.org/licenses/gpl-3.0.html> for details.
"""
Configuration handling for the ContextPM dashboard.

The dashboard ships with ``config.yaml`` in the project root.  This module loads
it, fills in any missing keys from the built-in defaults, and can write the
current settings back (``Save current settings as defaults`` in the UI).
"""
from __future__ import annotations

import copy
from pathlib import Path
from typing import Any, Dict, Optional

import yaml

PROJECT_ROOT = Path(__file__).resolve().parent.parent
CONFIG_FILE = PROJECT_ROOT / "config.yaml"


# Built-in defaults - used for every key that is missing from config.yaml so
# that a partially edited file never breaks the application.
DEFAULT_CONFIG: Dict[str, Any] = {
    "server": {"host": "127.0.0.1", "port": 8050, "debug": False},
    "paths": {
        "default_dataset": "data/eSense-2ndDataset-Raw_and_Events-Contextualized-Final.xlsx",
        "default_dataset_label": "eSense",
        "default_dataset_info_url": "https://zenodo.org/records/10223646",
        "outputs_dir": "outputs",
        "uploads_dir": "data/uploads",
    },
    "data": {
        "dataset_name": "eSense",
        "activity_column": "Contextualized_Activity",
        "timestamp_column": "StartTime",
        "day_column": "Date",
        "case_id_column": None,
        "duration_column": "Duration",
        "start_column": "StartTime",
        "end_column": "EndTime",
        "duration_output_column": "duration_seconds",
        "activity_column_to_clean": "Activity",
        "clean_delimiter": " - ",
        "clean_keep_part": "first",
        "columns_to_keep": [],
        "group_column": "Stress Level",
        "group_values": None,
        "include_overall_baseline": True,
        "filters": [],
    },
    "analysis": {
        "start_label": "Start",
        "end_label": "End",
        "top_n_outlier_days": 2,
        "score_method": "mean_abs_relative",
        "duration_score_weight": 0.7,
        "edge_score_weight": 0.3,
        "duration_baseline_overrides": {},
    },
    "graph": {
        "edge_threshold": 5,
        "keep_self_loops": True,
        "export_gexf": True,
        "export_day_graphs": True,
        "export_csv_for_days": True,
        "add_metric_text_to_labels": True,
        "node_size_source_min": 0,
        "node_size_source_max": 86400,
        "node_size_target_min": 50,
        "node_size_target_max": 1000,
        "start_end_node_size": 100,
        "min_edge_weight": 0.001,
        "layout_x_gap": 620,
        "layout_y_gap": 240,
    },
    "style": {
        "node_colors": {
            "default": "#A3A3A3",
            "default_border": "#ffffff",
            "start_end": "#d9d9d9",
            "keyword_rules": [],
            "explicit": {},
        },
        "hidden_nodes": [],
        "node_labels": {},
        "palette": [
            {"name": "Blue", "hex": "#3B82F6"},
            {"name": "Green", "hex": "#22C55E"},
            {"name": "Yellow", "hex": "#FACC15"},
            {"name": "Red", "hex": "#EF4444"},
            {"name": "Orange", "hex": "#F97316"},
            {"name": "Purple", "hex": "#A855F7"},
            {"name": "Pink", "hex": "#EC4899"},
            {"name": "Teal", "hex": "#14B8A6"},
            {"name": "Brown", "hex": "#A16207"},
            {"name": "Grey", "hex": "#A3A3A3"},
        ],
        "differential": {
            "negative_color": "#ff0000",
            "zero_color": "#a3a3a3",
            "positive_color": "#006400",
            "node_near_zero_ratio": 0.05,
            "edge_near_zero_ratio": 0.05,
            "node_border_color": "#000000",
            "day_edge_zero_color": "#808080",
        },
        "simple_edge_color": "#808080",
    },
    "viewer": {
        "node_diameter_px": [28, 130],
        "edge_width_px": [1.0, 10.0],
        "label_font_size": 12,
        "show_legend": True,
        "show_edge_labels": False,
        "background": "#ffffff",
    },
}

# Sections the browser is allowed to change and persist.
USER_SECTIONS = ("data", "analysis", "graph", "style", "viewer")


def deep_merge(base: Dict[str, Any], override: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    """Recursively merge ``override`` into a copy of ``base``."""
    result = copy.deepcopy(base)
    if not override:
        return result
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(result.get(key), dict):
            result[key] = deep_merge(result[key], value)
        else:
            result[key] = copy.deepcopy(value)
    return result


def load_config(path: Optional[Path] = None) -> Dict[str, Any]:
    """Load config.yaml (if present) on top of the built-in defaults."""
    path = Path(path) if path else CONFIG_FILE
    user_cfg: Dict[str, Any] = {}
    if path.exists():
        with open(path, "r", encoding="utf-8") as handle:
            user_cfg = yaml.safe_load(handle) or {}
    return deep_merge(DEFAULT_CONFIG, user_cfg)


def save_config(config: Dict[str, Any], path: Optional[Path] = None) -> Path:
    """Write the configuration back to config.yaml (keeps a .bak copy)."""
    path = Path(path) if path else CONFIG_FILE
    if path.exists():
        backup = path.with_suffix(".yaml.bak")
        backup.write_bytes(path.read_bytes())
    with open(path, "w", encoding="utf-8") as handle:
        handle.write("# ContextPM dashboard configuration - written by the dashboard.\n")
        handle.write("# See README.md for a description of every option.\n")
        yaml.safe_dump(config, handle, sort_keys=False, allow_unicode=True, default_flow_style=False)
    return path


def resolve_path(value: str | Path, base: Optional[Path] = None) -> Path:
    """Resolve a possibly relative path against the project root."""
    base = base or PROJECT_ROOT
    path = Path(str(value)).expanduser()
    if not path.is_absolute():
        path = base / path
    return path


def user_settings(config: Dict[str, Any]) -> Dict[str, Any]:
    """The part of the configuration that the browser edits."""
    return {section: copy.deepcopy(config.get(section, {})) for section in USER_SECTIONS}
