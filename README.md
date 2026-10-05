# HB_ContextPM – Human Behavior Contextualized Process Mining

> **Human Behavior Contextualized Process Mining (HB_ContextPM)**
>
> HB_ContextPM is developed by **Mohsen Shirali** and **Zahra Ahmadi** under the
> supervision of Prof. **Estefanía Serral** and Prof. **Jochen De Weerdt** at the
> Research Centre for Information Systems Engineering (LIRIS), KU Leuven.
>
> The human behavior contextualized process mining algorithm and the core analysis code were
> designed and developed by the authors. The web dashboard was prepared with the
> assistance of Claude (Anthropic).
>
> The project is developed as part of the HB-UniContext approach proposed in:
>
> Ahmadi, Z., Shirali, M., De Weerdt, J., Serral, E. (2026). Context-Enriched
> Process Discovery from IoT Data Sources for Human Behavioral Monitoring. In:
> Fill, HG., Wautelet, Y., Ralyté, J., Zdravkovic, J. (eds) The Practice of
> Enterprise Modeling. PoEM 2025. Lecture Notes in Business Information
> Processing, vol 570. Springer, Cham.
> https://doi.org/10.1007/978-3-032-12063-2_14
>
> HB_ContextPM is open source, released under the GNU General Public License
> v3.0 (see [LICENSE](LICENSE)). If you use it in your research or work, we
> appreciate an acknowledgement and a citation of the paper above.

---

HB_ContextPM turns a daily-activity log (for example, the
[eSense dataset](https://zenodo.org/records/10223646)) into **process maps**:
a *baseline* map discovered from all rows, one map per *group* (e.g.
`Stressful` / `Normal` days), *differential* maps that colour every node and
edge by how much a group deviates from the baseline, and one map per *day*.
The maps are exported as GraphML / GEXF (Gephi) and CSV tables, and can be
explored, restyled and saved as figures in an interactive viewer in the browser.

The analysis logic lives in `Baseline_and_KPI.py` and `PM_in_Graph.py`; the
dashboard (`contextpm_dashboard/`) wraps it in a local web application.

```
python run_dashboard.py          ->   http://127.0.0.1:8050
```

---

## 1. Installation

Requirements: **Python 3.9 or newer** (Windows, macOS or Linux) and a modern
browser (Chrome, Edge, Firefox, Safari).

```bash
# 1. get the code (or unzip the project folder)
cd HB_ContextPM

# 2. (recommended) create a virtual environment
python -m venv .venv
.venv\Scripts\activate          # Windows
source .venv/bin/activate       # macOS / Linux

# 3. install the dependencies
pip install -r requirements.txt
```

`requirements.txt` installs Flask, pandas, numpy, openpyxl, networkx, PyYAML
and pm4py. **pm4py is optional**: it is used for the directly-follows discovery
when present; otherwise the dashboard uses the equivalent pandas implementation
contained in `PM_in_Graph.py` (both give the same graphs). If
`pip install pm4py` fails on your machine, remove that line from
`requirements.txt` and install the rest.

No internet connection is needed while the dashboard runs: the interactive
graph library (Cytoscape.js) is shipped in `contextpm_dashboard/static/vendor/`.

## 2. Starting the dashboard

```bash
python run_dashboard.py
```

The console prints the address (default **http://127.0.0.1:8050**) and the
browser opens automatically. Options:

| option | meaning |
|---|---|
| `--port 9000` | listen on another port |
| `--host 0.0.0.0` | make the dashboard reachable from other computers on your network |
| `--config other.yaml` | use another configuration file |
| `--no-browser` | do not open the browser automatically |
| `--debug` | verbose Flask errors |

Host and port can also be set permanently in `config.yaml` (`server:` section).
Stop the server with `Ctrl+C`.

## 3. Using the dashboard

The dashboard has three pages (tabs at the top). The **Log** button at the top
right opens the execution log (it opens automatically when a job starts) and
the **Credits** button shows the project credits on every page.

### Page 1 – Data & discovery

1. **Input dataset** – tick *Use the default dataset: eSense* or enter the
   full path of a `.xlsx` / `.xls` / `.csv` / `.parquet` file on your computer
   and press *Load file*, or upload a file with *Upload & load* (uploads are
   copied to `data/uploads/`). The *dataset name* is the name of the output
   folder (`outputs/<dataset name>/`).
2. **Columns to keep** – every column of the file is listed as a checkbox; the
   pre-selected ones come from `data.columns_to_keep` in `config.yaml`.
   Columns needed by the analysis (activity, timestamp, day, duration, group)
   are marked *required* and are always kept.
3. **Column roles** – which column holds the activity (the nodes of the maps),
   the timestamp (event order), the day (one trace per day), the case id
   (default: the day), the duration (`hh:mm:ss`, or derived from start/end
   columns), and which column is standardized (`"Sleeping - Night - Good"` →
   `"Sleeping"`).
4. **Grouping** – choose the grouping column in the dropdown (one process map
   per value, e.g. `Stress Level`), untick values you do not want, and keep
   *Discover the baseline map from all rows* ticked to get the differential maps.
5. **Row filters** – optional include/exclude rules on column values.
6. **Analysis options** – number of "most different days" per group, the
   day-difference score and its weights, and the labels of the artificial
   Start/End nodes.

Press **▶ Discover process maps**. The job runs in the background; the log
panel shows the progress. When it finishes:

* **Discovery summary** lists the groups, the number of days, activities and
  transitions, the engine used (PM4Py or pandas) and the most different days
  per group.
* **Outputs & artifacts** lists every file produced (filter by category or by
  name). Click a file to preview it: CSV tables are shown as tables, saved
  figures as images, GraphML/GEXF/JSON files as text with an *Open in viewer*
  button. Every file can be downloaded.
* A green **next-step** box offers a button to continue with page 2.

*Save current settings as defaults* writes the settings of pages 1 and 2 to
`config.yaml` (a copy of the previous file is kept as `config.yaml.bak`).
*Reload defaults from config.yaml* discards unsaved changes.

### Page 2 – Process-map styling

* **Node colours & visibility** – all nodes (activities) of the discovered
  maps are listed with their current colour. Tick one or more nodes (use the
  filter box, *Select all* / *Select filtered*) and click one of the palette
  colours (blue, green, yellow, red, orange, purple, …) or choose a custom
  colour and press *Assign custom colour*. Assigned colours are stored under
  `style.node_colors.explicit`; nodes without an assigned colour follow the
  keyword rules (`sleeping` → blue, `hygiening` → orange, `using phone` →
  yellow, `eating` → purple) and the default grey.
  The **switch at the end of each row** shows or hides the node: hidden nodes
  (and their transitions) are left out of every process map and disappear from
  the label list. *Show all nodes* switches them all back on. The list is
  stored under `style.hidden_nodes`. (To remove an activity from the event log
  itself, so that the transitions around it are re-connected, use a row filter
  on page 1 instead.)
* **Node labels** – edit the label shown for each visible node. *Append the
  average duration per day* adds e.g. `7h30m` to the label.
* **Process-map options** – edge threshold (keep the N strongest edges around
  each node; untick for the full topology), self-loops, GEXF export, per-day
  maps, node-size mapping (seconds → Gephi size), Start/End size, minimum edge
  weight and the spacing of the automatic layered layout.
* **Differential colour scale** – the colours of the differential maps encode
  the difference values; they are fixed in the viewer and can only be changed
  in `config.yaml` (`style.differential`). The *near-zero band* (how close to
  the baseline a value must be to get the neutral grey colour, in % of the
  largest difference) can be adjusted here for nodes and edges.

Press **▶ Run: build the graph files** to regenerate all GraphML / GEXF / CSV
files with the chosen styling (fast: the discovery is not repeated). The
generated maps are listed at the bottom with *open in viewer* buttons, followed
by a next-step box that leads to page 3.

### Page 3 – Interactive viewer

Choose a graph in the dropdown (baseline, group maps, differential maps, day
maps, edited graphs). The map is drawn with the layout stored in the file.

* **Move nodes**: drag them. Drag the background to pan, scroll to zoom,
  *Fit* to re-centre.
* **Select**: click a node or edge, shift+click to add, shift+drag to
  box-select; *Select all nodes* / *Select all edges* in the side panel.
* **Colours**: with nodes selected, click a palette colour or choose a custom
  one and press *Apply to selected nodes*; the same for edges. For
  *differential* and *day* maps the colour controls are disabled – their colours
  are determined by the differential values.
* **Edge thickness**: enter a width in pixels and apply it to the selected
  edges, or change the global *Edge thickness scale* slider. *Node size scale*,
  *Label size*, label position, curved/straight edges, arrows, edge value
  labels and the background colour are also available.
* **Layout**: *Original (from file)*, layered (breadth-first), force-directed,
  circle, concentric or grid; press *Apply layout*.
* **Legend**: the *Legend* checkbox shows / hides the legend (node colours,
  edge-colour scale, edge thickness, node size). The legend is included in the
  saved figure when it is visible.
* **Save figure (PNG)**: downloads a high-resolution PNG of the current view
  (with the legend) and also stores it in `outputs/<dataset>/figures/`.
* **Save layout & style**: writes the current node positions, colours and edge
  widths as GraphML / GEXF / JSON to `outputs/<dataset>/edited/…__edited.*`
  (the original files are kept). Edited graphs appear in the dropdown and can
  be opened in Gephi.
* **Details**: click a node or edge to see its values (average duration per
  day, baseline, difference, …).

## 4. What the analysis computes

For every row the duration in seconds is derived, and the rows are split by the
grouping column. Then, for all rows (baseline) and for every group:

* **Node KPI** – average total duration per day of every activity
  (`avg_duration_per_day`); it drives the node size and the label text.
* **Edge KPI** – average number of transitions per day for every directly-follows
  pair (`avg_transitions_per_day`); it drives the edge thickness.
* **Process map** – the directly-follows graph of the rows (PM4Py or pandas),
  with artificial Start / End nodes, optionally reduced to the N strongest edges
  per node (`edge_threshold`).
* **Differential map (group vs. overall)** – node colour = group average minus
  overall average duration (red = less, grey = about equal, green = more); edge
  colour = difference of the transition averages, edge thickness = |difference|.
* **Day maps (day vs. group baseline)** – the same colouring for every single
  day of a group, plus the *most different days* per group (score combining the
  duration and transition differences with the configured weights).
* **Tables** – activity summary, average event counts, day-wise difference
  tables for durations and transitions, most different days.

## 5. Output files

Everything is written to `outputs/<dataset name>/` (the folder is emptied at
every discovery, except `figures/` and `edited/`):

```
outputs/eSense/
  prepared_data.csv                      rows after cleaning / filtering / column selection
  all_groups_top_2_days.csv              most different days of every group
  discovery_settings.json, graph_settings.json
  overall_data/
    overall_data__baseline.graphml/.gexf/.json   baseline map (all rows)
    overall_data_avg_duration_per_day.csv, overall_data_avg_transitions_per_day.csv
  <Group>/
    <Group>_activity_summary.csv, <Group>_avg_duration_per_day.csv,
    <Group>_avg_event_count_per_day.csv, <Group>_duration_difference_vs_baseline.csv,
    <Group>_edge_difference_vs_baseline.csv, <Group>_top_2_days.csv
    simple_group_graph/<Group>__simple.*  group map
    differential_vs_overall/<Group>__differential_vs_overall.*
    days/<Group>__<day>.*                 one map per day (+ nodes/edges CSV)
  figures/                                PNG figures saved from the viewer
  edited/                                 graphs saved from the viewer (positions / colours)
```

`.json` files next to the graphs are the bundles used by the interactive
viewer (same nodes, edges, positions, colours and legend data).

**Gephi**: open the `.gexf` files (File → Open). They carry the node positions,
sizes and colours (`viz` attributes); the GraphML files carry the same values as
plain attributes (`x`, `y`, `size`, `fill_color`, `edge_color`, `weight`, …).

## 6. Configuration (`config.yaml`)

All defaults live in `config.yaml` (comments inside the file explain every
key). Main sections:

| section | content |
|---|---|
| `server` | `host`, `port`, `debug` |
| `paths` | default dataset and its information link, outputs folder, uploads folder |
| `data` | dataset name, column roles, `columns_to_keep`, cleaning, `group_column`, `group_values`, `filters` |
| `analysis` | Start/End labels, most-different-days options, `duration_baseline_overrides` (manual baseline values per group) |
| `graph` | edge threshold, self-loops, GEXF / day-map export, node-size mapping, layout spacing |
| `style` | `node_colors` (default, Start/End, keyword rules, explicit assignments), `hidden_nodes`, `node_labels`, `palette`, `differential` colour scale, `simple_edge_color` |
| `viewer` | pixel ranges for node diameters and edge widths, label size, legend default |

Missing keys fall back to built-in defaults, so the file can be shortened.
*Save current settings as defaults* rewrites the file (without the comments);
the previous version is kept as `config.yaml.bak`.

## 7. Project layout

```
run_dashboard.py            start the web server
config.yaml                 defaults
requirements.txt
LICENSE                     GNU General Public License v3.0
Baseline_and_KPI.py         KPI / baseline / differential table functions
PM_in_Graph.py              discovery, colouring, layout and GraphML/GEXF/CSV export functions
contextpm_dashboard/
  server.py                 Flask application and JSON API
  pipeline.py               stage A (prepare, KPIs, discovery) and stage B (styling, export)
  viewer_graphs.py          graph bundles for the viewer, saving edited graphs
  artifacts.py              listing / previewing output files
  jobs.py                   background job runner and log capture
  config.py                 configuration loading / saving
  static/                   index.html, app.js, styles.css, vendor/cytoscape.min.js
data/                       default dataset (eSense) and uploads
```

## 8. Troubleshooting

* **"Address already in use"** – another program uses the port: start with
  `python run_dashboard.py --port 8060`.
* **The default dataset is missing** – put the Excel file at the path given in
  `paths.default_dataset`, or load your own file with *Load file*.
* **Excel files cannot be read** – make sure `openpyxl` is installed
  (`pip install openpyxl`).
* **pm4py is not installed** – the log shows `engine: pandas`; the maps are the
  same. Install it with `pip install pm4py` if you want to use it.
* **A job fails** – open the log panel: the last lines contain the error
  (typically a wrong column role or a duration column that is not `hh:mm:ss`).
* **Windows paths** – paste the path as shown by the Explorer, e.g.
  `C:\Users\me\data\file.xlsx`; quotes around it are fine.
* **Other computers cannot reach the dashboard** – start with
  `--host 0.0.0.0` (or set `server.host`) and allow the port in the firewall.

## 9. License

Copyright (C) 2026 Mohsen Shirali and Zahra Ahmadi (LIRIS, KU Leuven).

ContextPM is free software: you can redistribute it and/or modify it under the
terms of the GNU General Public License version 3 as published by the Free
Software Foundation. It is distributed in the hope that it will be useful, but
WITHOUT ANY WARRANTY; see the [LICENSE](LICENSE) file for the full text.

The bundled Cytoscape.js library (`contextpm_dashboard/static/vendor/`) is
distributed under its own MIT license, and the eSense dataset under the terms
of its [Zenodo record](https://zenodo.org/records/10223646).
