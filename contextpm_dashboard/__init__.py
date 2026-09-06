# ContextPM - Contextualized Process Mining
# Copyright (C) 2026 Mohsen Shirali and Zahra Ahmadi (LIRIS, KU Leuven)
#
# This program is free software: you can redistribute it and/or modify it
# under the terms of the GNU General Public License version 3 as published by
# the Free Software Foundation. It is distributed WITHOUT ANY WARRANTY; see the
# LICENSE file or <https://www.gnu.org/licenses/gpl-3.0.html> for details.
"""
ContextPM dashboard - a local web dashboard around the contextualized
process-mining pipeline (``Baseline_and_KPI.py`` and ``PM_in_Graph.py``).

Run ``python run_dashboard.py`` from the project folder and open the printed
address in a browser.
"""
from __future__ import annotations

import sys
from pathlib import Path

# The analysis modules live in the project root next to this package; make sure
# they are importable no matter where the dashboard is started from.
_PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

__version__ = "1.0.0"
