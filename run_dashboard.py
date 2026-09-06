#!/usr/bin/env python
# ContextPM - Contextualized Process Mining
# Copyright (C) 2026 Mohsen Shirali and Zahra Ahmadi (LIRIS, KU Leuven)
#
# This program is free software: you can redistribute it and/or modify it
# under the terms of the GNU General Public License version 3 as published by
# the Free Software Foundation. It is distributed WITHOUT ANY WARRANTY; see the
# LICENSE file or <https://www.gnu.org/licenses/gpl-3.0.html> for details.
"""
Start the ContextPM web dashboard.

    python run_dashboard.py                 # uses host/port from config.yaml
    python run_dashboard.py --port 9000     # override the port
    python run_dashboard.py --no-browser    # do not open the browser automatically

Then open the printed address (default http://127.0.0.1:8050) in your browser.
"""
from __future__ import annotations

import argparse
import logging
import sys
import threading
import webbrowser
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from contextpm_dashboard.config import CONFIG_FILE, load_config  # noqa: E402
from contextpm_dashboard.server import create_app  # noqa: E402


def main() -> None:
    parser = argparse.ArgumentParser(description="ContextPM - contextualized process mining dashboard")
    parser.add_argument("--config", default=str(CONFIG_FILE), help="path of the configuration file (default: config.yaml)")
    parser.add_argument("--host", default=None, help="interface to bind (default: server.host in config.yaml)")
    parser.add_argument("--port", type=int, default=None, help="port to listen on (default: server.port in config.yaml)")
    parser.add_argument("--no-browser", action="store_true", help="do not open the dashboard in the default browser")
    parser.add_argument("--debug", action="store_true", help="Flask debug mode (verbose errors)")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)-7s %(name)s: %(message)s", datefmt="%H:%M:%S")
    logging.getLogger("werkzeug").setLevel(logging.WARNING)

    config_path = Path(args.config)
    config = load_config(config_path)
    host = args.host or config["server"].get("host", "127.0.0.1")
    port = int(args.port or config["server"].get("port", 8050))
    debug = bool(args.debug or config["server"].get("debug", False))

    app = create_app(config, config_path)
    browser_host = "127.0.0.1" if host in ("0.0.0.0", "", "::") else host
    url = f"http://{browser_host}:{port}/"

    print("=" * 66)
    print("  ContextPM dashboard")
    print(f"  configuration : {config_path}")
    print(f"  open in browser: {url}")
    print("  stop with Ctrl+C")
    print("=" * 66)

    if not args.no_browser:
        threading.Timer(1.2, lambda: webbrowser.open(url)).start()

    app.run(host=host, port=port, debug=debug, threaded=True, use_reloader=False)


if __name__ == "__main__":
    main()
