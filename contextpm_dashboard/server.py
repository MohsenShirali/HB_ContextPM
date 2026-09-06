# ContextPM - Contextualized Process Mining
# Copyright (C) 2026 Mohsen Shirali and Zahra Ahmadi (LIRIS, KU Leuven)
#
# This program is free software: you can redistribute it and/or modify it
# under the terms of the GNU General Public License version 3 as published by
# the Free Software Foundation. It is distributed WITHOUT ANY WARRANTY; see the
# LICENSE file or <https://www.gnu.org/licenses/gpl-3.0.html> for details.
"""
Flask application exposing the pipeline and the interactive viewer.

All state lives in one ``AppState`` object because the dashboard is a local,
single-user tool.  Long-running work (discovery, graph export) runs in a
background thread; the browser polls ``/api/status`` and ``/api/logs``.
"""
from __future__ import annotations

import base64
import copy
import datetime as dt
import logging
import re
import threading
from pathlib import Path
from typing import Any, Dict, Optional

from flask import Flask, abort, jsonify, request, send_file, send_from_directory
from flask.json.provider import DefaultJSONProvider
from werkzeug.utils import secure_filename

from . import __version__
from . import artifacts as art
from . import pipeline
from . import viewer_graphs as vg
from .config import (
    CONFIG_FILE,
    PROJECT_ROOT,
    USER_SECTIONS,
    deep_merge,
    load_config,
    resolve_path,
    save_config,
    user_settings,
)
from .jobs import JobManager, install_log_buffer

log = logging.getLogger("contextpm.server")
STATIC_DIR = Path(__file__).resolve().parent / "static"
ALLOWED_DATASET_SUFFIXES = {".csv", ".txt", ".xlsx", ".xls", ".parquet"}


class SafeJSONProvider(DefaultJSONProvider):
    """JSON provider that never fails on numpy / pandas scalar types."""

    @staticmethod
    def default(obj: Any) -> Any:  # type: ignore[override]
        if hasattr(obj, "item"):
            try:
                return obj.item()
            except Exception:  # pragma: no cover - defensive
                pass
        try:
            return DefaultJSONProvider.default(obj)
        except TypeError:
            return str(obj)


class AppState:
    def __init__(self, config: Dict[str, Any], config_path: Optional[Path] = None) -> None:
        self.lock = threading.RLock()
        self.config = config
        self.config_path = Path(config_path) if config_path else CONFIG_FILE
        self.settings: Dict[str, Any] = user_settings(config)
        self.dataset_df = None
        self.dataset_path: Optional[Path] = None
        self.dataset_label: str = ""
        self.dataset_source: str = ""
        self.dataset_info: Optional[Dict[str, Any]] = None
        self.discovery: Optional[pipeline.Discovery] = None
        self.discovery_summary: Optional[Dict[str, Any]] = None
        self.export_summary: Optional[Dict[str, Any]] = None
        self.jobs = JobManager()
        self.logs = install_log_buffer()

    # -- paths -------------------------------------------------------------
    def outputs_root(self) -> Path:
        return resolve_path(self.config["paths"]["outputs_dir"])

    def dataset_output_dir(self, dataset_name: Optional[str] = None) -> Path:
        name = dataset_name or self.settings["data"].get("dataset_name") or "dataset"
        return self.outputs_root() / pipeline.safe_slug(name)

    def default_dataset_path(self) -> Path:
        return resolve_path(self.config["paths"]["default_dataset"])

    # -- settings ----------------------------------------------------------
    def merge_settings(self, incoming: Optional[Dict[str, Any]]) -> Dict[str, Any]:
        if not incoming:
            return self.settings
        with self.lock:
            for section in USER_SECTIONS:
                if section in incoming and isinstance(incoming[section], dict):
                    current = self.settings.get(section, {})
                    if section == "style":
                        # replace the maps completely so deleted entries disappear
                        merged = deep_merge(current, incoming[section])
                        for key in ("node_labels", "hidden_nodes"):
                            if key in incoming[section]:
                                merged[key] = copy.deepcopy(incoming[section][key])
                        if "node_colors" in incoming[section] and "explicit" in incoming[section]["node_colors"]:
                            merged["node_colors"]["explicit"] = copy.deepcopy(incoming[section]["node_colors"]["explicit"])
                        if "palette" in incoming[section]:
                            merged["palette"] = copy.deepcopy(incoming[section]["palette"])
                        self.settings[section] = merged
                    elif section == "data":
                        merged = deep_merge(current, incoming[section])
                        for key in ("columns_to_keep", "filters", "group_values"):
                            if key in incoming[section]:
                                merged[key] = copy.deepcopy(incoming[section][key])
                        self.settings[section] = merged
                    else:
                        self.settings[section] = deep_merge(current, incoming[section])
            return self.settings

    def snapshot(self) -> Dict[str, Any]:
        job = self.jobs.status()
        pm4py_available = pipeline.pm4py is not None
        return {
            "version": __version__,
            "config_file": str(self.config_path),
            "project_root": str(PROJECT_ROOT),
            "paths": {
                "default_dataset": str(self.default_dataset_path()),
                "default_dataset_label": self.config["paths"].get("default_dataset_label", "default dataset"),
                "default_dataset_info_url": self.config["paths"].get("default_dataset_info_url", ""),
                "default_dataset_exists": self.default_dataset_path().exists(),
                "outputs_dir": str(self.outputs_root()),
            },
            "settings": self.settings,
            "dataset": self.dataset_info,
            "discovery": self.discovery_summary,
            "export": self.export_summary,
            "job": job,
            "pm4py_available": pm4py_available,
            "datasets_with_outputs": art.list_dataset_folders(self.outputs_root()),
        }


def create_app(config: Optional[Dict[str, Any]] = None, config_path: Optional[Path] = None) -> Flask:
    config = config or load_config(config_path)
    state = AppState(config, config_path)
    app = Flask(__name__, static_folder=str(STATIC_DIR), static_url_path="/static")
    app.json = SafeJSONProvider(app)
    app.config["MAX_CONTENT_LENGTH"] = 512 * 1024 * 1024
    app.state = state  # type: ignore[attr-defined]

    # ------------------------------------------------------------------ pages
    @app.get("/")
    def index():
        return send_from_directory(str(STATIC_DIR), "index.html")

    # ------------------------------------------------------------------ state
    @app.get("/api/state")
    def get_state():
        return jsonify(state.snapshot())

    @app.get("/api/status")
    def get_status():
        return jsonify(
            {
                "job": state.jobs.status(),
                "dataset_loaded": state.dataset_df is not None,
                "discovery_ready": state.discovery is not None,
                "export_ready": state.export_summary is not None,
                "discovery": state.discovery_summary,
                "export": state.export_summary,
            }
        )

    @app.get("/api/logs")
    def get_logs():
        since = int(request.args.get("since", 0) or 0)
        items, latest = state.logs.since(since)
        return jsonify({"items": items, "latest": latest, "running": state.jobs.is_running()})

    @app.post("/api/logs/clear")
    def clear_logs():
        state.logs.clear()
        return jsonify({"ok": True})

    # --------------------------------------------------------------- settings
    @app.get("/api/settings")
    def get_settings():
        return jsonify(state.settings)

    @app.post("/api/settings")
    def post_settings():
        payload = request.get_json(silent=True) or {}
        state.merge_settings(payload.get("settings", payload))
        return jsonify(state.settings)

    @app.post("/api/settings/save")
    def save_settings():
        payload = request.get_json(silent=True) or {}
        state.merge_settings(payload.get("settings", payload))
        with state.lock:
            for section in USER_SECTIONS:
                state.config[section] = copy.deepcopy(state.settings[section])
            path = save_config(state.config, state.config_path)
        log.info("Settings saved as defaults to %s", path)
        return jsonify({"ok": True, "path": str(path)})

    @app.post("/api/settings/reset")
    def reset_settings():
        with state.lock:
            state.config = load_config(state.config_path)
            state.settings = user_settings(state.config)
        log.info("Settings reloaded from %s", state.config_path)
        return jsonify(state.settings)

    # ---------------------------------------------------------------- dataset
    def _load_dataset_file(path: Path, label: str, source: str) -> Dict[str, Any]:
        if not path.exists():
            raise FileNotFoundError(f"File not found: {path}")
        if path.suffix.lower() not in ALLOWED_DATASET_SUFFIXES:
            raise ValueError(f"Unsupported file type '{path.suffix}'. Use .csv, .txt, .xlsx, .xls or .parquet.")
        log.info("Loading dataset %s ...", path)
        frame = pipeline.load_dataset(path)
        info = pipeline.describe_dataframe(frame)
        info.update({"path": str(path), "label": label, "source": source, "loaded_at": dt.datetime.now().isoformat(timespec="seconds")})
        with state.lock:
            state.dataset_df = frame
            state.dataset_path = path
            state.dataset_label = label
            state.dataset_source = source
            state.dataset_info = info
            state.discovery = None
            state.discovery_summary = None
            state.export_summary = None
        log.info("Dataset loaded: %d rows, %d columns.", info["rows"], len(info["columns"]))
        return info

    @app.post("/api/dataset/load")
    def load_dataset():
        if state.jobs.is_running():
            return jsonify({"error": "A job is running - wait for it to finish before loading another dataset."}), 409
        try:
            if request.files and "file" in request.files:
                upload = request.files["file"]
                filename = secure_filename(upload.filename or "dataset.xlsx")
                uploads_dir = resolve_path(state.config["paths"].get("uploads_dir", "data/uploads"))
                uploads_dir.mkdir(parents=True, exist_ok=True)
                target = uploads_dir / filename
                upload.save(target)
                info = _load_dataset_file(target, filename, "upload")
            else:
                payload = request.get_json(silent=True) or {}
                source = payload.get("source", "default")
                if source == "default":
                    path = state.default_dataset_path()
                    info = _load_dataset_file(path, state.config["paths"].get("default_dataset_label", path.name), "default")
                else:
                    raw_path = str(payload.get("path", "")).strip().strip('"')
                    if not raw_path:
                        return jsonify({"error": "Please enter the path of the dataset file."}), 400
                    path = resolve_path(raw_path)
                    info = _load_dataset_file(path, path.name, "path")
        except Exception as exc:  # noqa: BLE001 - report to the UI
            log.error("Could not load the dataset: %s", exc)
            return jsonify({"error": str(exc)}), 400
        return jsonify({"dataset": info, "settings": state.settings})

    # ------------------------------------------------------------------- jobs
    def _start(name: str, target):
        try:
            job = state.jobs.start(name, target)
        except RuntimeError as exc:
            return jsonify({"error": str(exc)}), 409
        return jsonify({"job": job})

    @app.post("/api/discover")
    def discover():
        if state.dataset_df is None:
            return jsonify({"error": "Load a dataset first."}), 400
        payload = request.get_json(silent=True) or {}
        state.merge_settings(payload.get("settings"))
        settings = copy.deepcopy(state.settings)
        frame = state.dataset_df
        label = state.dataset_label
        config = copy.deepcopy(state.config)

        def target():
            discovery = pipeline.run_discovery(frame, settings, config, dataset_label=label)
            with state.lock:
                state.discovery = discovery
                state.discovery_summary = discovery.summary()
                state.export_summary = None
            export = pipeline.run_export(discovery, settings)
            with state.lock:
                state.export_summary = export
            return {"discovery": state.discovery_summary, "export": {"count": export["count"], "output_dir": export["output_dir"]}}

        return _start("Discover process maps", target)

    @app.post("/api/build")
    def build_graphs():
        if state.discovery is None:
            return jsonify({"error": "Run the discovery (page 1) first."}), 400
        payload = request.get_json(silent=True) or {}
        state.merge_settings(payload.get("settings"))
        settings = copy.deepcopy(state.settings)
        discovery = state.discovery

        def target():
            export = pipeline.run_export(discovery, settings)
            with state.lock:
                state.export_summary = export
            return {"export": {"count": export["count"], "output_dir": export["output_dir"]}}

        return _start("Build graph files", target)

    @app.get("/api/nodes")
    def nodes():
        return jsonify({"nodes": pipeline.default_node_table(state.discovery, state.settings), "discovery_ready": state.discovery is not None})

    # -------------------------------------------------------------- artifacts
    def _dataset_root(dataset: Optional[str]) -> Path:
        return state.dataset_output_dir(dataset)

    def _safe_path(dataset: Optional[str], rel: str) -> Path:
        root = _dataset_root(dataset).resolve()
        if not rel or rel.startswith("/") or rel.startswith("\\") or re.search(r"(^|[\\/])\.\.([\\/]|$)", rel):
            abort(400, "Invalid path")
        path = (root / rel).resolve()
        if path != root and root not in path.parents:
            abort(400, "Invalid path")
        if not path.exists() or not path.is_file():
            abort(404, f"File not found: {rel}")
        return path

    @app.get("/api/artifacts")
    def list_artifacts():
        dataset = request.args.get("dataset") or None
        root = _dataset_root(dataset)
        return jsonify(
            {
                "dataset": root.name,
                "root": str(root),
                "datasets": art.list_dataset_folders(state.outputs_root()),
                "files": art.scan_outputs(root),
            }
        )

    @app.get("/api/artifacts/file")
    def get_artifact_file():
        path = _safe_path(request.args.get("dataset"), request.args.get("path", ""))
        download = request.args.get("download") in ("1", "true", "yes")
        return send_file(path, as_attachment=download, download_name=path.name, max_age=0)

    @app.get("/api/artifacts/preview")
    def preview_artifact():
        path = _safe_path(request.args.get("dataset"), request.args.get("path", ""))
        ext = path.suffix.lower()
        try:
            if ext == ".csv":
                return jsonify({"type": "csv", **art.csv_preview(path)})
            if ext in {".json", ".txt", ".log", ".graphml", ".gexf"}:
                return jsonify({"type": "text", **art.text_preview(path)})
        except Exception as exc:  # noqa: BLE001
            return jsonify({"error": str(exc)}), 400
        return jsonify({"type": "binary"})

    # ----------------------------------------------------------------- graphs
    @app.get("/api/graphs")
    def list_graphs():
        dataset = request.args.get("dataset") or None
        root = _dataset_root(dataset)
        bundles = vg.list_bundles(root)
        known_stems = {b["id"] for b in bundles}
        extra = []
        if root.exists():
            for path in sorted(root.rglob("*.graphml")):
                stem = path.relative_to(root).with_suffix("").as_posix()
                if stem in known_stems:
                    continue
                extra.append(
                    {
                        "id": stem, "name": path.stem, "kind": "custom", "kind_title": vg.KIND_TITLES["custom"],
                        "group": path.relative_to(root).parts[0] if len(path.relative_to(root).parts) > 1 else None,
                        "day": None, "locked_colors": False, "edited": "edited" in stem, "counts": {},
                        "files": {"graphml": path.relative_to(root).as_posix()}, "path": None,
                    }
                )
        return jsonify({"dataset": root.name, "graphs": bundles + extra, "viewer": state.settings.get("viewer", {})})

    @app.get("/api/graphs/<path:graph_id>")
    def get_graph(graph_id: str):
        dataset = request.args.get("dataset") or None
        root = _dataset_root(dataset)
        if ".." in graph_id.split("/"):
            abort(400)
        try:
            bundle, _ = vg.find_bundle(root, graph_id)
        except FileNotFoundError:
            for suffix in (".graphml", ".gexf"):
                candidate = root / (graph_id + suffix)
                if candidate.exists():
                    try:
                        bundle = vg.bundle_from_graph_file(candidate, root)
                        break
                    except Exception as exc:  # noqa: BLE001
                        return jsonify({"error": f"Could not read {candidate.name}: {exc}"}), 400
            else:
                return jsonify({"error": f"Graph '{graph_id}' not found."}), 404
        bundle["viewer"] = state.settings.get("viewer", {})
        return jsonify(bundle)

    @app.post("/api/graphs/<path:graph_id>/save")
    def save_graph(graph_id: str):
        dataset = request.args.get("dataset") or None
        root = _dataset_root(dataset)
        if ".." in graph_id.split("/"):
            abort(400)
        payload = request.get_json(silent=True) or {}
        try:
            bundle, _ = vg.find_bundle(root, graph_id)
        except FileNotFoundError:
            candidate = next((root / (graph_id + s) for s in (".graphml", ".gexf") if (root / (graph_id + s)).exists()), None)
            if candidate is None:
                return jsonify({"error": f"Graph '{graph_id}' not found."}), 404
            bundle = vg.bundle_from_graph_file(candidate, root)
        edited = vg.apply_edits(bundle, payload.get("edits") or {})
        # Edited graphs live under <output>/edited/... so a rebuild never overwrites them.
        base_id = graph_id
        if base_id.startswith("edited/"):
            base_id = base_id[len("edited/"):]
        if base_id.endswith("__edited"):
            base_id = base_id[:-len("__edited")]
        stem = root / "edited" / (base_id + "__edited")
        edited["id"] = stem.relative_to(root).as_posix()
        edited["name"] = (bundle.get("name") or base_id).replace(" (edited)", "") + " (edited)"
        files = vg.export_bundle(edited, root, stem, export_gexf=bool(state.settings["graph"].get("export_gexf", True)))
        log.info("Edited graph saved: %s", ", ".join(files.values()))
        return jsonify({"ok": True, "graph": vg.summarize(edited, files["json"]), "files": files})

    @app.post("/api/figures")
    def save_figure():
        payload = request.get_json(silent=True) or {}
        image = str(payload.get("image", ""))
        match = re.match(r"^data:image/(png|jpeg);base64,(.+)$", image, re.DOTALL)
        if not match:
            return jsonify({"error": "Expected a base64 PNG data URL."}), 400
        dataset = request.args.get("dataset") or payload.get("dataset") or None
        root = _dataset_root(dataset)
        figures_dir = root / "figures"
        figures_dir.mkdir(parents=True, exist_ok=True)
        base = secure_filename(str(payload.get("name") or payload.get("graph_id") or "figure").replace("/", "__")) or "figure"
        stamp = dt.datetime.now().strftime("%Y%m%d-%H%M%S")
        path = figures_dir / f"{base}__{stamp}.{match.group(1)}"
        path.write_bytes(base64.b64decode(match.group(2)))
        log.info("Figure saved: %s", path)
        return jsonify({"ok": True, "path": path.relative_to(root).as_posix(), "absolute": str(path)})

    @app.errorhandler(400)
    @app.errorhandler(404)
    @app.errorhandler(405)
    @app.errorhandler(413)
    @app.errorhandler(500)
    def api_error(err):  # noqa: ANN001
        if request.path.startswith("/api/"):
            code = getattr(err, "code", 500) or 500
            description = getattr(err, "description", None) or str(err)
            return jsonify({"error": str(description)}), code
        return err

    return app
