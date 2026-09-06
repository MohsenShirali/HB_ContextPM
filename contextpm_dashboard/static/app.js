/*
 * ContextPM - Contextualized Process Mining
 * Copyright (C) 2026 Mohsen Shirali and Zahra Ahmadi (LIRIS, KU Leuven)
 * Released under the GNU General Public License version 3 (see LICENSE).
 */
/* ContextPM dashboard - browser logic (no build step, plain JavaScript). */
(function () {
  'use strict';

  // ------------------------------------------------------------------ utils
  const $ = (sel, root) => (root || document).querySelector(sel);
  const $$ = (sel, root) => Array.from((root || document).querySelectorAll(sel));
  const clone = (obj) => JSON.parse(JSON.stringify(obj));
  const esc = (s) => String(s == null ? '' : s).replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
  const clamp = (v, lo, hi) => Math.max(lo, Math.min(hi, v));
  const isBlank = (v) => v === null || v === undefined || String(v).trim() === '';

  function el(tag, attrs, children) {
    const node = document.createElement(tag);
    if (attrs) {
      for (const [k, v] of Object.entries(attrs)) {
        if (k === 'class') node.className = v;
        else if (k === 'html') node.innerHTML = v;
        else if (k === 'text') node.textContent = v;
        else if (k.startsWith('on')) node.addEventListener(k.slice(2), v);
        else if (v !== null && v !== undefined && v !== false) node.setAttribute(k, v === true ? '' : v);
      }
    }
    for (const child of [].concat(children || [])) {
      if (child === null || child === undefined) continue;
      node.appendChild(typeof child === 'string' ? document.createTextNode(child) : child);
    }
    return node;
  }

  function fmtDuration(seconds) {
    if (seconds === null || seconds === undefined || isNaN(seconds)) return '';
    const s = Math.round(Number(seconds));
    const h = Math.floor(s / 3600), m = Math.floor((s % 3600) / 60);
    if (h && m) return `${h}h${m}m`;
    if (h) return `${h}h`;
    return `${m}m`;
  }
  const fmtNum = (v, d = 2) => (v === null || v === undefined || isNaN(v)) ? '' : Number(v).toFixed(d).replace(/\.?0+$/, '');
  const fmtSize = (b) => b > 1048576 ? `${(b / 1048576).toFixed(1)} MB` : b > 1024 ? `${(b / 1024).toFixed(0)} KB` : `${b} B`;

  let toastTimer = null;
  function toast(message, kind) {
    const box = $('#toast');
    box.textContent = message;
    box.className = 'toast' + (kind ? ' ' + kind : '');
    box.hidden = false;
    clearTimeout(toastTimer);
    toastTimer = setTimeout(() => { box.hidden = true; }, kind === 'error' ? 7000 : 3500);
  }

  async function apiFetch(url, options) {
    const res = await fetch(url, options || {});
    const text = await res.text();
    let data = null;
    try { data = text ? JSON.parse(text) : null; } catch (e) { data = { error: text }; }
    if (!res.ok) throw new Error((data && data.error) || `${res.status} ${res.statusText}`);
    return data;
  }
  const api = {
    get: (url) => apiFetch(url),
    post: (url, body) => apiFetch(url, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body || {}) }),
    postForm: (url, form) => apiFetch(url, { method: 'POST', body: form }),
  };

  // ------------------------------------------------------------------ state
  const state = {
    app: null,
    settings: null,
    defaults: null,
    dataset: null,
    discovery: null,
    exportSummary: null,
    nodes: [],
    graphs: [],
    artifacts: [],
    artifactDataset: null,
    artifactCategory: 'all',
    activeArtifact: null,
    logSeq: 0,
    jobTimer: null,
    nodeSelection: new Set(),
  };

  const datasetParam = () => state.artifactDataset ? `dataset=${encodeURIComponent(state.artifactDataset)}` : '';

  // ------------------------------------------------------------------ pages
  function showPage(page) {
    if (!$(`#page-${page}`)) page = 'data';
    $$('.page').forEach((p) => p.classList.toggle('active', p.id === `page-${page}`));
    $$('.tab').forEach((t) => t.classList.toggle('active', t.dataset.page === page));
    if (location.hash !== `#${page}`) history.replaceState(null, '', `#${page}`);
    if (page === 'viewer' && viewer.cy) setTimeout(() => { viewer.cy.resize(); drawLegend(); }, 50);
  }

  // ------------------------------------------------------------------ init
  async function init() {
    bindEvents();
    await refreshState();
    startLogPolling();
    showPage((location.hash || '#data').slice(1));
  }

  async function refreshState() {
    const app = await api.get('/api/state');
    state.app = app;
    state.settings = app.settings;
    if (!state.defaults) state.defaults = clone(app.settings);
    state.dataset = app.dataset;
    state.discovery = app.discovery;
    state.exportSummary = app.export;
    if (!state.artifactDataset) state.artifactDataset = null;
    renderAll();
    if (app.job && app.job.running) watchJob();
    updateJobPill(app.job);
  }

  function renderAll() {
    renderDatasetCard();
    renderColumns();
    renderRoles();
    renderGrouping();
    renderFilters();
    renderAnalysis();
    renderDiscoverySummary();
    renderGraphOptions();
    renderDiffScale();
    renderPalettes();
    renderBuildSummary();
    loadNodes();
    loadArtifacts();
    loadGraphList();
  }

  // ------------------------------------------------------------------ jobs
  function updateJobPill(job) {
    const pill = $('#jobStatus');
    const cur = job && job.current;
    if (!cur) { pill.textContent = 'idle'; pill.className = 'pill'; return; }
    if (cur.status === 'running') { pill.textContent = `running: ${cur.name}`; pill.className = 'pill running'; }
    else if (cur.status === 'done') { pill.textContent = `done: ${cur.name} (${cur.duration_seconds}s)`; pill.className = 'pill done'; }
    else { pill.textContent = `failed: ${cur.name}`; pill.className = 'pill failed'; }
    $('#btnDiscover').disabled = !!(cur && cur.status === 'running');
    $('#btnBuild').disabled = !!(cur && cur.status === 'running');
  }

  function watchJob(onDone) {
    clearInterval(state.jobTimer);
    state.jobTimer = setInterval(async () => {
      try {
        const status = await api.get('/api/status');
        updateJobPill(status.job);
        const cur = status.job.current;
        if (!cur || cur.status !== 'running') {
          clearInterval(state.jobTimer);
          state.jobTimer = null;
          if (cur && cur.status === 'failed') toast(`Job failed: ${cur.error}`, 'error');
          if (onDone) await onDone(cur);
        }
      } catch (e) { /* server briefly unavailable - keep polling */ }
    }, 1000);
  }

  // ------------------------------------------------------------------ logs
  function startLogPolling() {
    const poll = async () => {
      try {
        const data = await api.get(`/api/logs?since=${state.logSeq}`);
        if (data.items.length) {
          const body = $('#logBody');
          for (const item of data.items) {
            const line = el('div', { class: `log-line ${item.level}` });
            line.appendChild(el('span', { class: 't', text: `[${item.time}] ` }));
            line.appendChild(document.createTextNode(`${item.level.padEnd(7)} ${item.message}`));
            body.appendChild(line);
          }
          while (body.children.length > 3000) body.removeChild(body.firstChild);
          state.logSeq = data.latest;
          if ($('#autoScroll').checked) body.scrollTop = body.scrollHeight;
        }
      } catch (e) { /* ignore */ }
    };
    poll();
    setInterval(poll, 1000);
  }
  function openLog(open) {
    const panel = $('#logPanel');
    const isOpen = open === undefined ? !panel.classList.contains('open') : open;
    panel.classList.toggle('open', isOpen);
    document.body.classList.toggle('log-open', isOpen);
  }

  // ================================================================== PAGE 1
  function renderDatasetCard() {
    const paths = state.app.paths;
    $('#defaultDatasetLabel').textContent = paths.default_dataset_label;
    const link = $('#defaultDatasetLink');
    $('#defaultDatasetInfo').hidden = !paths.default_dataset_info_url;
    link.href = paths.default_dataset_info_url || '#';
    link.textContent = paths.default_dataset_info_url || '';
    $('#useDefaultDataset').disabled = !paths.default_dataset_exists;
    $('#useDefaultDataset').title = paths.default_dataset_exists ? '' : 'The default dataset file is missing (see paths.default_dataset in config.yaml)';
    $('#datasetName').value = state.settings.data.dataset_name || '';
    const ds = state.dataset;
    const info = $('#datasetInfo');
    if (!ds) { info.className = 'info-box muted'; info.textContent = 'No dataset loaded yet.'; $('#useDefaultDataset').checked = false; return; }
    info.className = 'info-box';
    const name = ds.source === 'default' ? paths.default_dataset_label : ds.label;
    info.innerHTML = `Loaded dataset: <b>${esc(name)}</b> — ${ds.rows} rows × ${ds.columns.length} columns`;
    $('#useDefaultDataset').checked = ds.source === 'default';
    if (ds.source === 'path') $('#datasetPath').value = ds.path;
  }

  function datasetColumns() {
    return state.dataset ? state.dataset.columns.map((c) => c.name) : [];
  }
  function columnInfo(name) {
    return state.dataset ? state.dataset.columns.find((c) => c.name === name) : null;
  }
  function requiredColumns() {
    const d = state.settings.data;
    const req = new Set([d.activity_column, d.timestamp_column, d.day_column, d.case_id_column, d.duration_column, d.group_column, d.duration_output_column]);
    if (isBlank(d.duration_column)) { req.add(d.start_column); req.add(d.end_column); }
    req.delete(null); req.delete(undefined); req.delete('');
    return req;
  }

  function renderColumns(selected) {
    const box = $('#columnsList');
    const cols = datasetColumns();
    if (!cols.length) { box.className = 'check-grid muted'; box.textContent = 'Load a dataset to see its columns.'; $('#colsCount').textContent = ''; return; }
    const derived = state.settings.data.duration_output_column || 'duration_seconds';
    const all = cols.includes(derived) ? cols : cols.concat([derived]);
    let chosen = selected || state.settings.data.columns_to_keep || [];
    if (!chosen.some((c) => all.includes(c))) chosen = all.slice();  // config does not match this file -> keep everything
    const required = requiredColumns();
    box.className = 'check-grid';
    box.innerHTML = '';
    for (const name of all) {
      const badge = name === derived && !cols.includes(name) ? el('span', { class: 'badge derived', text: 'derived' }) : (required.has(name) ? el('span', { class: 'badge', text: 'required' }) : null);
      box.appendChild(el('label', {}, [el('input', { type: 'checkbox', value: name, checked: chosen.includes(name) || required.has(name) }), el('span', { text: name }), badge]));
    }
    box.addEventListener('change', updateColsCount);
    updateColsCount();
  }
  function updateColsCount() {
    const boxes = $$('#columnsList input');
    if (!boxes.length) return;
    $('#colsCount').textContent = `${boxes.filter((b) => b.checked).length} of ${boxes.length} columns selected`;
  }

  function fillSelect(select, options, value, extra) {
    select.innerHTML = '';
    for (const [v, label] of (extra || [])) select.appendChild(el('option', { value: v, text: label }));
    for (const name of options) select.appendChild(el('option', { value: name, text: name }));
    const wanted = value == null ? '' : String(value);
    if (wanted && !Array.from(select.options).some((o) => o.value === wanted)) select.appendChild(el('option', { value: wanted, text: `${wanted} (not in file)` }));
    select.value = wanted;
  }

  function renderRoles() {
    const d = state.settings.data;
    const cols = datasetColumns();
    fillSelect($('#colActivity'), cols, d.activity_column);
    fillSelect($('#colTimestamp'), cols, d.timestamp_column);
    fillSelect($('#colDay'), cols, d.day_column, [['', '(derive from the timestamp column)']]);
    fillSelect($('#colCase'), cols, d.case_id_column, [['', '(same as the day column)']]);
    fillSelect($('#colDuration'), cols, d.duration_column, [['', '(derive from start / end columns)']]);
    fillSelect($('#colStart'), cols, d.start_column, [['', '(none)']]);
    fillSelect($('#colEnd'), cols, d.end_column, [['', '(none)']]);
    fillSelect($('#colClean'), cols, d.activity_column_to_clean, [['', '(no standardization)']]);
    $('#durationOutput').value = d.duration_output_column || 'duration_seconds';
    $('#cleanDelimiter').value = d.clean_delimiter == null ? ' - ' : d.clean_delimiter;
    $('#cleanKeepPart').value = d.clean_keep_part || 'first';
  }

  function renderGrouping() {
    const d = state.settings.data;
    fillSelect($('#groupColumn'), datasetColumns(), d.group_column, [['', '(no grouping - one map for all rows)']]);
    $('#includeOverall').checked = d.include_overall_baseline !== false;
    renderGroupValues();
  }
  function renderGroupValues() {
    const box = $('#groupValues');
    const col = $('#groupColumn').value;
    const info = col ? columnInfo(col) : null;
    box.innerHTML = '';
    if (!col) { box.className = 'check-grid muted'; box.textContent = 'No grouping column selected.'; return; }
    if (!info) { box.className = 'check-grid muted'; box.textContent = 'Load a dataset to list the group values.'; return; }
    if (!info.values) { box.className = 'check-grid muted'; box.textContent = `${info.n_unique} distinct values - all of them will be used as groups.`; return; }
    const wanted = state.settings.data.group_values;
    box.className = 'check-grid';
    for (const v of info.values) {
      const label = String(v);
      box.appendChild(el('label', {}, [el('input', { type: 'checkbox', value: label, checked: !wanted || wanted.map(String).includes(label) }), el('span', { text: label })]));
    }
  }

  function renderFilters() {
    const list = $('#filtersList');
    const rules = state.settings.data.filters || [];
    list.innerHTML = '';
    if (!rules.length) { list.className = 'filters muted'; list.textContent = 'No row filters.'; }
    else {
      list.className = 'filters';
      rules.forEach((rule, i) => {
        list.appendChild(el('span', { class: 'filter-chip' }, [
          `${rule.column}: ${rule.mode === 'include' ? 'keep only' : 'exclude'} [${(rule.values || []).join(', ')}]`,
          el('button', { title: 'remove', onclick: () => { state.settings.data.filters.splice(i, 1); renderFilters(); } }, '×'),
        ]));
      });
    }
    const filterable = (state.dataset ? state.dataset.columns : []).filter((c) => c.values).map((c) => c.name);
    fillSelect($('#filterColumn'), filterable, $('#filterColumn').value || '', [['', '(choose a column)']]);
    renderFilterValues();
  }
  function renderFilterValues() {
    const box = $('#filterValues');
    const info = columnInfo($('#filterColumn').value);
    box.innerHTML = '';
    if (!info || !info.values) { box.className = 'check-grid muted small'; box.textContent = 'Choose a column with a limited number of distinct values.'; return; }
    box.className = 'check-grid small';
    for (const v of info.values) box.appendChild(el('label', {}, [el('input', { type: 'checkbox', value: String(v) }), el('span', { text: String(v) })]));
  }

  function renderAnalysis() {
    const a = state.settings.analysis;
    $('#topNDays').value = a.top_n_outlier_days;
    $('#scoreMethod').value = a.score_method || 'mean_abs_relative';
    $('#durationWeight').value = a.duration_score_weight;
    $('#edgeWeight').value = a.edge_score_weight;
    $('#startLabel').value = a.start_label || 'Start';
    $('#endLabel').value = a.end_label || 'End';
  }

  function collectPage1() {
    const d = state.settings.data, a = state.settings.analysis;
    const v = (sel) => $(sel).value;
    const vn = (sel) => ($(sel).value === '' ? null : $(sel).value);
    d.dataset_name = v('#datasetName').trim() || 'dataset';
    if ($$('#columnsList input').length) d.columns_to_keep = $$('#columnsList input:checked').map((i) => i.value);
    d.activity_column = v('#colActivity');
    d.timestamp_column = v('#colTimestamp');
    d.day_column = vn('#colDay');
    d.case_id_column = vn('#colCase');
    d.duration_column = vn('#colDuration');
    d.duration_output_column = v('#durationOutput').trim() || 'duration_seconds';
    d.start_column = vn('#colStart');
    d.end_column = vn('#colEnd');
    d.activity_column_to_clean = vn('#colClean');
    d.clean_delimiter = v('#cleanDelimiter');
    d.clean_keep_part = v('#cleanKeepPart');
    d.group_column = vn('#groupColumn');
    const gv = $$('#groupValues input');
    d.group_values = gv.length && gv.some((i) => !i.checked) ? gv.filter((i) => i.checked).map((i) => i.value) : null;
    d.include_overall_baseline = $('#includeOverall').checked;
    a.top_n_outlier_days = parseInt(v('#topNDays'), 10) || 2;
    a.score_method = v('#scoreMethod');
    a.duration_score_weight = parseFloat(v('#durationWeight')) || 0;
    a.edge_score_weight = parseFloat(v('#edgeWeight')) || 0;
    a.start_label = v('#startLabel').trim() || 'Start';
    a.end_label = v('#endLabel').trim() || 'End';
  }

  async function loadDataset(payload, form) {
    try {
      $('#datasetInfo').textContent = 'Loading…';
      const res = form ? await api.postForm('/api/dataset/load', form) : await api.post('/api/dataset/load', payload);
      state.dataset = res.dataset;
      state.discovery = null;
      state.exportSummary = null;
      renderDatasetCard(); renderColumns(); renderRoles(); renderGrouping(); renderFilters(); renderDiscoverySummary();
      toast(`Loaded ${res.dataset.rows} rows from ${res.dataset.label}`, 'ok');
    } catch (e) {
      $('#datasetInfo').className = 'info-box';
      $('#datasetInfo').innerHTML = `<span style="color:#b91c1c">${esc(e.message)}</span>`;
      toast(e.message, 'error');
    }
  }

  async function discover() {
    if (!state.dataset) { toast('Load a dataset first (section 1).', 'error'); return; }
    collectPage1();
    collectPage2();
    updateJobPill({ current: { status: 'running', name: 'Discover process maps' } });
    try {
      await api.post('/api/discover', { settings: state.settings });
      openLog(true);
      watchJob(async (job) => {
        await refreshState();
        if (job && job.status === 'done') toast('Discovery finished - the maps and artifacts are listed on the right and on pages 2 and 3.', 'ok');
      });
    } catch (e) { toast(e.message, 'error'); api.get('/api/status').then((s) => updateJobPill(s.job)).catch(() => {}); }
  }

  function renderDiscoverySummary() {
    const box = $('#discoverySummary');
    const d = state.discovery;
    $('#nextStepData').hidden = !d;
    if (!d) { box.className = 'muted'; box.innerHTML = 'Nothing discovered yet. Load a dataset and press <b>Discover process maps</b>.'; return; }
    box.className = '';
    box.innerHTML = '';
    const kv = el('div', { class: 'kv' });
    const add = (k, v) => { kv.appendChild(el('b', { text: k })); kv.appendChild(el('span', { html: v })); };
    add('Dataset', `${esc(d.dataset)} — ${d.rows.raw} rows loaded, ${d.rows.prepared} rows after preparation`);
    add('Discovery engine', d.engine === 'pm4py' ? 'PM4Py (directly-follows graph)' : 'pandas directly-follows graph (PM4Py not available)');
    add('Columns', `activity=<code>${esc(d.columns.activity)}</code> timestamp=<code>${esc(d.columns.timestamp)}</code> day=<code>${esc(d.columns.day)}</code> case=<code>${esc(d.columns.case_id)}</code>${d.columns.group ? ` group=<code>${esc(d.columns.group)}</code>` : ''}`);
    add('Baseline (all rows)', d.overall.included ? `${d.overall.rows} rows, ${d.overall.days} days, ${d.overall.activities} activities, ${d.overall.transitions} transitions` : 'not included');
    add('Output folder', `<code>${esc(d.output_dir)}</code>`);
    add('Finished', d.created_at);
    box.appendChild(kv);
    const groups = el('div', { class: 'summary-groups' });
    for (const g of d.groups) {
      const top = (g.top_days || []).map((r) => {
        const day = r.Day || r.Date || r.day || Object.values(r)[1];
        return `${esc(day)} (score ${fmtNum(r.combined_score, 3)})`;
      }).join(', ');
      groups.appendChild(el('div', { class: 'summary-group' }, [
        el('h4', { text: `Group: ${g.name}` }),
        el('div', { class: 'small', html: `${g.rows} rows · ${g.days} days · ${g.activities} activities · ${g.transitions} transitions<br>Most different days: ${top || '-'}` }),
      ]));
    }
    box.appendChild(el('h3', { text: 'Groups' }));
    box.appendChild(groups);
    if (state.exportSummary) box.appendChild(el('p', { class: 'small muted', text: `${state.exportSummary.count} process maps exported (see the list of outputs below and page 3).` }));
  }

  // ------------------------------------------------------------ artifacts
  const ARTIFACT_CATEGORIES = [['all', 'All'], ['graph', 'Process maps'], ['table', 'Tables (CSV)'], ['figure', 'Saved figures'], ['viewer', 'Viewer JSON'], ['other', 'Other']];

  async function loadArtifacts() {
    try {
      const data = await api.get(`/api/artifacts?${datasetParam()}`);
      state.artifacts = data.files;
      state.artifactDataset = data.dataset;
      const sel = $('#artifactDataset');
      const names = data.datasets.includes(data.dataset) ? data.datasets : [data.dataset].concat(data.datasets);
      fillSelect(sel, names, data.dataset);
      renderArtifactChips();
      renderArtifactList();
    } catch (e) { /* no outputs yet */ }
  }
  function renderArtifactChips() {
    const chips = $('#artifactCategories');
    chips.innerHTML = '';
    for (const [key, label] of ARTIFACT_CATEGORIES) {
      const count = key === 'all' ? state.artifacts.length : state.artifacts.filter((f) => f.category === key).length;
      if (!count && key !== 'all') continue;
      chips.appendChild(el('button', { class: 'chip' + (state.artifactCategory === key ? ' active' : ''), onclick: () => { state.artifactCategory = key; renderArtifactChips(); renderArtifactList(); } }, `${label} (${count})`));
    }
  }
  function renderArtifactList() {
    const list = $('#artifactList');
    const needle = $('#artifactFilter').value.trim().toLowerCase();
    const files = state.artifacts.filter((f) => (state.artifactCategory === 'all' || f.category === state.artifactCategory) && (!needle || f.path.toLowerCase().includes(needle)));
    list.innerHTML = '';
    if (!files.length) { list.className = 'artifact-list muted'; list.textContent = state.artifacts.length ? 'No file matches the filter.' : 'No outputs yet - run the discovery first.'; return; }
    list.className = 'artifact-list';
    const byFolder = new Map();
    for (const f of files) { const key = f.folder || '(root)'; if (!byFolder.has(key)) byFolder.set(key, []); byFolder.get(key).push(f); }
    for (const [folder, items] of byFolder) {
      const details = el('details', { open: byFolder.size <= 6 || !folder.includes('/days') }, [el('summary', { text: `${folder} (${items.length})` })]);
      for (const f of items) {
        const item = el('div', { class: 'artifact-item' + (state.activeArtifact === f.path ? ' active' : ''), title: f.path, onclick: () => previewArtifact(f) }, [
          el('span', { class: `icon ${f.type}`, text: f.ext.replace('.', '') || 'file' }),
          el('span', { class: 'name', text: f.name }),
          el('span', { class: 'size', text: fmtSize(f.size) }),
        ]);
        details.appendChild(item);
      }
      list.appendChild(details);
    }
  }
  function artifactUrl(f, download) {
    return `/api/artifacts/file?${datasetParam()}&path=${encodeURIComponent(f.path)}${download ? '&download=1' : ''}`;
  }
  async function previewArtifact(f) {
    state.activeArtifact = f.path;
    $$('.artifact-item').forEach((n) => n.classList.toggle('active', n.title === f.path));
    $('#previewTitle').textContent = f.path;
    $('#previewTitle').className = '';
    const dl = $('#previewDownload');
    dl.href = artifactUrl(f, true); dl.hidden = false;
    const openBtn = $('#btnPreviewOpenViewer');
    const graphId = f.path.replace(/\.(graphml|gexf|json)$/i, '');
    const canOpen = ['graphml', 'gexf'].includes(f.type) || (f.type === 'json' && f.category === 'viewer');
    openBtn.hidden = !canOpen;
    openBtn.onclick = () => openInViewer(graphId);
    const body = $('#previewBody');
    body.innerHTML = '';
    try {
      if (f.type === 'image') {
        body.appendChild(el('img', { src: artifactUrl(f), alt: f.name }));
      } else if (f.type === 'csv') {
        const p = await api.get(`/api/artifacts/preview?${datasetParam()}&path=${encodeURIComponent(f.path)}`);
        body.appendChild(el('div', { class: 'muted small', text: `${p.shown_rows} of ${p.total_rows} rows shown` }));
        const table = el('table', { class: 'data' });
        table.appendChild(el('thead', {}, el('tr', {}, p.columns.map((c) => el('th', { text: c })))));
        table.appendChild(el('tbody', {}, p.rows.map((r) => el('tr', {}, r.map((c) => el('td', { text: c }))))));
        body.appendChild(el('div', { class: 'table-wrap' }, table));
      } else if (['graphml', 'gexf', 'json', 'text'].includes(f.type)) {
        const p = await api.get(`/api/artifacts/preview?${datasetParam()}&path=${encodeURIComponent(f.path)}`);
        if (canOpen) body.appendChild(el('p', { class: 'small muted', text: 'Use "Open in viewer" to display this process map interactively, or download it for Gephi.' }));
        body.appendChild(el('pre', { text: p.text + (p.truncated ? '\n… (truncated)' : '') }));
      } else {
        body.appendChild(el('p', { class: 'muted', text: 'No preview for this file type - use Download.' }));
      }
    } catch (e) { body.appendChild(el('p', { class: 'muted', text: e.message })); }
  }

  // ================================================================== PAGE 2
  function styleColors() { return state.settings.style.node_colors; }
  function startEnd() { const a = state.settings.analysis; return [a.start_label || 'Start', a.end_label || 'End']; }

  function resolveNodeColor(id) {
    const nc = styleColors();
    if (startEnd().includes(id)) return [nc.start_end || '#d9d9d9', 'start / end'];
    const explicit = nc.explicit || {};
    if (!isBlank(explicit[id])) return [explicit[id], 'assigned'];
    const lower = id.toLowerCase();
    for (const rule of nc.keyword_rules || []) {
      const kw = String(rule.keyword || '').toLowerCase().trim();
      if (kw && lower.includes(kw) && !isBlank(rule.color)) return [rule.color, `keyword "${kw}"`];
    }
    return [nc.default || '#A3A3A3', 'default'];
  }

  async function loadNodes() {
    try {
      const data = await api.get('/api/nodes');
      state.nodes = data.discovery_ready ? data.nodes : [];
    } catch (e) { state.nodes = []; }
    renderNodeColorList();
    renderNodeLabelList();
    renderColorRules();
  }

  function renderPalettes() {
    for (const [id, handler] of [['#palette', assignColorToSelection], ['#viewerPalette', (hex) => applyNodeColor(hex)]]) {
      const box = $(id);
      box.innerHTML = '';
      for (const p of state.settings.style.palette || []) {
        box.appendChild(el('button', { type: 'button', title: `${p.name} ${p.hex}`, style: `background:${p.hex}`, onclick: () => handler(p.hex) }));
      }
    }
  }

  // ---- node visibility (style.hidden_nodes): hidden nodes are left out of every exported map
  function hiddenNodes() {
    const style = state.settings.style;
    if (!Array.isArray(style.hidden_nodes)) style.hidden_nodes = [];
    return style.hidden_nodes;
  }
  function isHidden(id) { return hiddenNodes().includes(id); }
  function setHidden(id, hidden) {
    const list = hiddenNodes();
    const idx = list.indexOf(id);
    if (hidden && idx < 0) list.push(id);
    if (!hidden && idx >= 0) list.splice(idx, 1);
  }
  function updateHiddenCount() {
    const known = new Set(state.nodes.map((n) => n.id));
    const hidden = hiddenNodes().filter((id) => known.has(id));
    $('#hiddenCount').textContent = hidden.length ? `${hidden.length} node(s) hidden from all maps: ${hidden.join(', ')}` : 'All nodes are displayed.';
  }

  function renderNodeColorList() {
    const box = $('#nodeColorList');
    box.innerHTML = '';
    if (!state.nodes.length) { box.className = 'node-list muted'; box.textContent = 'Run the discovery (page 1) to list the nodes of the process maps.'; updateHiddenCount(); return; }
    box.className = 'node-list';
    const needle = $('#nodeFilter').value.trim().toLowerCase();
    for (const n of state.nodes) {
      if (needle && !n.id.toLowerCase().includes(needle)) continue;
      const [color, source] = resolveNodeColor(n.id);
      const hidden = isHidden(n.id) && !n.is_start_end;
      const select = el('input', { type: 'checkbox', checked: state.nodeSelection.has(n.id), title: 'select for colour assignment' });
      const row = el('div', { class: 'node-row' + (state.nodeSelection.has(n.id) ? ' selected' : '') + (hidden ? ' hidden-node' : ''), 'data-id': n.id });
      select.addEventListener('change', () => { if (select.checked) state.nodeSelection.add(n.id); else state.nodeSelection.delete(n.id); row.classList.toggle('selected', select.checked); });
      const toggle = el('input', { type: 'checkbox', checked: !hidden, disabled: !!n.is_start_end });
      toggle.addEventListener('change', () => { setHidden(n.id, !toggle.checked); row.classList.toggle('hidden-node', !toggle.checked); updateHiddenCount(); renderNodeLabelList(); });
      const sw = el('label', { class: 'switch', title: n.is_start_end ? 'Start / End are always displayed' : 'Show or hide this node in every process map' }, [toggle, el('span', { class: 'track' })]);
      row.append(
        select,
        el('span', { class: 'swatch', style: `background:${color}` }),
        el('span', { class: 'name', text: n.id, title: n.id }),
        el('span', { class: 'src', text: source }),
        el('span', { class: 'dur', text: n.avg_duration_text || '', title: 'average duration per day (all rows)' }),
        sw,
      );
      row.addEventListener('click', (ev) => {
        if (ev.target === select || ev.target.closest('.switch')) return;
        select.checked = !select.checked;
        select.dispatchEvent(new Event('change'));
      });
      box.appendChild(row);
    }
    updateHiddenCount();
  }
  function renderColorRules() {
    const nc = styleColors();
    const rules = (nc.keyword_rules || []).map((r) => `<span class="swatch" style="background:${esc(r.color)}"></span> name contains "${esc(r.keyword)}"`).join(' &nbsp; ');
    $('#colorRules').innerHTML = `${rules || 'no keyword rules'} &nbsp; <span class="swatch" style="background:${esc(nc.default)}"></span> otherwise &nbsp; <span class="swatch" style="background:${esc(nc.start_end)}"></span> ${esc(startEnd().join(' / '))}. Colours assigned above override these rules (stored under style.node_colors.explicit in config.yaml).`;
  }
  function assignColorToSelection(hex) {
    if (!state.nodeSelection.size) { toast('Tick one or more nodes first, then choose a colour.', 'error'); return; }
    const nc = styleColors();
    nc.explicit = nc.explicit || {};
    for (const id of state.nodeSelection) {
      if (startEnd().includes(id)) nc.start_end = hex; else nc.explicit[id] = hex;
    }
    renderNodeColorList();
    renderColorRules();
    toast(`Colour ${hex} assigned to ${state.nodeSelection.size} node(s). Press "Run" to rebuild the graph files.`, 'ok');
  }

  function renderNodeLabelList() {
    const box = $('#nodeLabelList');
    box.innerHTML = '';
    $('#addMetricText').checked = state.settings.graph.add_metric_text_to_labels !== false;
    if (!state.nodes.length) { box.className = 'node-list muted'; box.textContent = 'Run the discovery (page 1) first.'; return; }
    box.className = 'node-list';
    const labels = state.settings.style.node_labels || {};
    for (const n of state.nodes) {
      if (isHidden(n.id) && !n.is_start_end) continue;  // hidden nodes are not part of the maps
      const input = el('input', { type: 'text', value: labels[n.id] != null ? labels[n.id] : n.id, onchange: (ev) => {
        const value = ev.target.value.trim();
        state.settings.style.node_labels = state.settings.style.node_labels || {};
        if (!value || value === n.id) delete state.settings.style.node_labels[n.id]; else state.settings.style.node_labels[n.id] = value;
        if (!value) ev.target.value = n.id;
      } });
      box.appendChild(el('div', { class: 'node-row label-row' }, [el('span', { class: 'name', text: n.id, title: n.id }), input]));
    }
  }

  function renderGraphOptions() {
    const g = state.settings.graph;
    $('#edgeThreshold').value = g.edge_threshold == null ? 5 : g.edge_threshold;
    $('#noThreshold').checked = g.edge_threshold == null;
    $('#edgeThreshold').disabled = g.edge_threshold == null;
    $('#keepSelfLoops').checked = g.keep_self_loops !== false;
    $('#exportGexf').checked = g.export_gexf !== false;
    $('#exportDayGraphs').checked = g.export_day_graphs !== false;
    $('#exportCsvDays').checked = g.export_csv_for_days !== false;
    $('#sizeSourceMin').value = g.node_size_source_min;
    $('#sizeSourceMax').value = g.node_size_source_max;
    $('#sizeTargetMin').value = g.node_size_target_min;
    $('#sizeTargetMax').value = g.node_size_target_max;
    $('#startEndSize').value = g.start_end_node_size;
    $('#minEdgeWeight').value = g.min_edge_weight;
    $('#layoutXGap').value = g.layout_x_gap;
    $('#layoutYGap').value = g.layout_y_gap;
  }
  function renderDiffScale() {
    const d = state.settings.style.differential;
    $('#diffScaleInfo').innerHTML = `
      <span class="item"><span class="swatch" style="background:${esc(d.negative_color)}"></span> below baseline</span>
      <span class="bar" style="background:linear-gradient(90deg, ${esc(d.negative_color)}, ${esc(d.zero_color)}, ${esc(d.positive_color)})"></span>
      <span class="item"><span class="swatch" style="background:${esc(d.positive_color)}"></span> above baseline</span>
      <span class="item muted">near-zero band: nodes ${Math.round(d.node_near_zero_ratio * 100)} %, edges ${Math.round(d.edge_near_zero_ratio * 100)} % of the largest difference</span>`;
    $('#nodeNearZero').value = Math.round((d.node_near_zero_ratio || 0) * 100);
    $('#edgeNearZero').value = Math.round((d.edge_near_zero_ratio || 0) * 100);
  }
  function collectPage2() {
    const g = state.settings.graph;
    const num = (sel, fallback) => { const v = parseFloat($(sel).value); return isNaN(v) ? fallback : v; };
    g.edge_threshold = $('#noThreshold').checked ? null : Math.max(1, parseInt($('#edgeThreshold').value, 10) || 5);
    g.keep_self_loops = $('#keepSelfLoops').checked;
    g.export_gexf = $('#exportGexf').checked;
    g.export_day_graphs = $('#exportDayGraphs').checked;
    g.export_csv_for_days = $('#exportCsvDays').checked;
    g.add_metric_text_to_labels = $('#addMetricText').checked;
    g.node_size_source_min = num('#sizeSourceMin', 0);
    g.node_size_source_max = num('#sizeSourceMax', 86400);
    g.node_size_target_min = num('#sizeTargetMin', 50);
    g.node_size_target_max = num('#sizeTargetMax', 1000);
    g.start_end_node_size = num('#startEndSize', 100);
    g.min_edge_weight = num('#minEdgeWeight', 0.001);
    g.layout_x_gap = num('#layoutXGap', 620);
    g.layout_y_gap = num('#layoutYGap', 240);
    const diff = state.settings.style.differential;
    diff.node_near_zero_ratio = clamp(num('#nodeNearZero', 5), 0, 100) / 100;
    diff.edge_near_zero_ratio = clamp(num('#edgeNearZero', 5), 0, 100) / 100;
  }

  async function buildGraphs() {
    if (!state.discovery) { toast('Run the discovery on page 1 first.', 'error'); return; }
    collectPage2();
    updateJobPill({ current: { status: 'running', name: 'Build graph files' } });
    try {
      await api.post('/api/build', { settings: state.settings });
      openLog(true);
      watchJob(async (job) => {
        await refreshState();
        if (job && job.status === 'done') toast('Graph files rebuilt. Open page 3 to view them.', 'ok');
      });
    } catch (e) { toast(e.message, 'error'); api.get('/api/status').then((s) => updateJobPill(s.job)).catch(() => {}); }
  }

  function renderBuildSummary() {
    const box = $('#buildSummary');
    const ex = state.exportSummary;
    box.innerHTML = '';
    $('#nextStepStyle').hidden = !(ex && ex.graphs);
    if (!ex || !ex.graphs) { box.className = 'muted'; box.textContent = 'No graphs built yet.'; return; }
    box.className = '';
    box.appendChild(el('p', { class: 'small muted', text: `${ex.count} process maps in ${ex.output_dir}` }));
    const groups = new Map();
    for (const g of ex.graphs) { const key = g.kind_title; if (!groups.has(key)) groups.set(key, []); groups.get(key).push(g); }
    for (const [title, items] of groups) {
      const wrap = el('div', { class: 'summary-group' }, [el('h4', { text: `${title} (${items.length})` })]);
      const links = el('div', { class: 'graph-links' });
      for (const g of items.slice(0, 12)) links.appendChild(el('button', { class: 'btn btn-sm', onclick: () => openInViewer(g.id) }, g.name));
      if (items.length > 12) links.appendChild(el('span', { class: 'muted small', text: `… and ${items.length - 12} more (see page 3)` }));
      wrap.appendChild(links);
      box.appendChild(wrap);
    }
  }

  async function saveDefaults() {
    collectPage1();
    collectPage2();
    try {
      const res = await api.post('/api/settings/save', { settings: state.settings });
      state.defaults = clone(state.settings);
      toast(`Settings saved to ${res.path}`, 'ok');
    } catch (e) { toast(e.message, 'error'); }
  }
  async function resetDefaults() {
    try {
      state.settings = await api.post('/api/settings/reset', {});
      state.defaults = clone(state.settings);
      state.nodeSelection.clear();
      renderAll();
      toast('Settings reloaded from config.yaml', 'ok');
    } catch (e) { toast(e.message, 'error'); }
  }

  // ================================================================== PAGE 3
  const viewer = {
    cy: null,
    bundle: null,
    graphId: null,
    edits: null,
    posScale: 1,  // file coordinates (Gephi units) -> screen pixels; positions are divided by it when saved
    opts: { nodeScale: 1, edgeScale: 1, fontSize: 12, labelPosition: 'bottom', curved: true, arrows: true, edgeLabels: false, legend: true, bg: '#ffffff' },
    scale: { sizeMin: 50, sizeMax: 1000, wMin: 0, wMax: 1, dMin: 28, dMax: 130, pxMin: 1, pxMax: 10 },
  };

  function openInViewer(graphId) {
    showPage('viewer');
    const sel = $('#graphSelect');
    if (!Array.from(sel.options).some((o) => o.value === graphId)) sel.appendChild(el('option', { value: graphId, text: graphId }));
    sel.value = graphId;
    openGraph(graphId);
  }

  async function loadGraphList() {
    try {
      const data = await api.get(`/api/graphs?${datasetParam()}`);
      state.graphs = data.graphs;
      const sel = $('#graphSelect');
      const current = sel.value;
      sel.innerHTML = '';
      sel.appendChild(el('option', { value: '', text: state.graphs.length ? '— select a graph —' : '— no graphs yet: run the discovery on page 1 —' }));
      const groups = new Map();
      for (const g of state.graphs) { if (!groups.has(g.kind_title)) groups.set(g.kind_title, []); groups.get(g.kind_title).push(g); }
      for (const [title, items] of groups) {
        const og = el('optgroup', { label: title });
        for (const g of items) og.appendChild(el('option', { value: g.id, text: g.name + (g.edited ? ' [edited]' : '') }));
        sel.appendChild(og);
      }
      if (current && Array.from(sel.options).some((o) => o.value === current)) sel.value = current;
    } catch (e) { /* ignore */ }
  }

  async function openGraph(graphId) {
    if (!graphId) return;
    const msg = $('#viewerMessage');
    msg.hidden = false; msg.textContent = 'Loading…';
    try {
      const bundle = await api.get(`/api/graphs/${graphId.split('/').map(encodeURIComponent).join('/')}?${datasetParam()}`);
      viewer.bundle = bundle;
      viewer.graphId = graphId;
      viewer.edits = { positions: {}, node_colors: {}, edge_colors: {}, edge_weights: {} };
      const v = state.settings.viewer || {};
      if (bundle.viewer) {
        viewer.scale.dMin = (bundle.viewer.node_diameter_px || [28, 130])[0];
        viewer.scale.dMax = (bundle.viewer.node_diameter_px || [28, 130])[1];
        viewer.scale.pxMin = (bundle.viewer.edge_width_px || [1, 10])[0];
        viewer.scale.pxMax = (bundle.viewer.edge_width_px || [1, 10])[1];
      }
      viewer.opts.fontSize = parseInt($('#fontSize').value, 10) || v.label_font_size || 12;
      buildCy();
      msg.hidden = true;
      renderGraphInfo();
      updateSelectionInfo();
      $('#elementDetails').textContent = 'Click a node or an edge to see its values.';
    } catch (e) { msg.textContent = e.message; toast(e.message, 'error'); }
  }

  function computeScales() {
    const b = viewer.bundle, g = state.settings.graph || {};
    const sizes = b.nodes.map((n) => n.size);
    let sMin = Number(g.node_size_target_min), sMax = Number(g.node_size_target_max);
    if (!(sMax > sMin) || sizes.some((s) => s < sMin - 1e-6 || s > sMax + 1e-6)) { sMin = Math.min(...sizes); sMax = Math.max(...sizes); }
    if (!(sMax > sMin)) sMax = sMin + 1;
    const weights = b.edges.map((e) => e.weight).filter((w) => w > 0.0015);
    let wMin = weights.length ? Math.min(...weights) : 0, wMax = weights.length ? Math.max(...weights) : 1;
    if (!(wMax > wMin)) { wMin = 0; wMax = wMax || 1; }
    Object.assign(viewer.scale, { sizeMin: sMin, sizeMax: sMax, wMin, wMax });
  }
  function diameterFor(size) {
    const s = viewer.scale;
    const t = clamp((size - s.sizeMin) / (s.sizeMax - s.sizeMin), 0, 1);
    return (s.dMin + t * (s.dMax - s.dMin)) * viewer.opts.nodeScale;
  }
  function widthFor(weight) {
    const s = viewer.scale;
    const t = clamp((weight - s.wMin) / (s.wMax - s.wMin), 0, 1);
    return (s.pxMin + t * (s.pxMax - s.pxMin)) * viewer.opts.edgeScale;
  }
  function weightForWidth(px) {
    const s = viewer.scale;
    const t = clamp((px / viewer.opts.edgeScale - s.pxMin) / (s.pxMax - s.pxMin), 0, 1);
    return s.wMin + t * (s.wMax - s.wMin);
  }
  function edgeLabelFor(e) {
    const diff = e.metrics && e.metrics.difference_value;
    if (viewer.bundle.locked_colors && diff !== null && diff !== undefined) return (diff > 0 ? '+' : '') + fmtNum(diff, 2);
    return fmtNum(e.weight, 2);
  }

  function makeStyle() {
    const o = viewer.opts;
    return [
      { selector: 'node', style: {
        'width': 'data(diameter)', 'height': 'data(diameter)', 'background-color': 'data(color)', 'border-color': 'data(borderColor)', 'border-width': 'data(borderWidth)',
        'label': 'data(label)', 'font-size': o.fontSize, 'font-family': 'Segoe UI, Roboto, Helvetica, Arial, sans-serif',
        'text-valign': o.labelPosition, 'text-halign': 'center', 'text-margin-y': o.labelPosition === 'bottom' ? 6 : (o.labelPosition === 'top' ? -6 : 0),
        'text-wrap': 'wrap', 'text-max-width': '170px', 'color': '#111827', 'text-outline-color': '#ffffff', 'text-outline-width': 2,
      } },
      { selector: 'node:selected', style: { 'overlay-color': '#2563eb', 'overlay-opacity': 0.25, 'overlay-padding': 6 } },
      { selector: 'edge', style: {
        'width': 'data(width)', 'line-color': 'data(color)', 'target-arrow-color': 'data(color)', 'target-arrow-shape': o.arrows ? 'triangle' : 'none',
        'arrow-scale': 1, 'curve-style': o.curved ? 'bezier' : 'straight', 'control-point-step-size': 40,
        'label': o.edgeLabels ? 'data(weightLabel)' : '', 'font-size': Math.max(6, o.fontSize - 2), 'text-rotation': 'autorotate', 'color': '#374151',
        'text-outline-color': '#ffffff', 'text-outline-width': 2, 'text-background-opacity': 0,
      } },
      { selector: 'edge:loop', style: { 'curve-style': 'bezier', 'loop-direction': '-45deg', 'loop-sweep': '60deg' } },
      { selector: 'edge:selected', style: { 'overlay-color': '#2563eb', 'overlay-opacity': 0.3, 'overlay-padding': 4 } },
    ];
  }

  function hasPositions(b) {
    const xs = b.nodes.map((n) => n.x), ys = b.nodes.map((n) => n.y);
    return b.nodes.length > 1 && (Math.max(...xs) - Math.min(...xs) > 0 || Math.max(...ys) - Math.min(...ys) > 0);
  }

  /* File coordinates are Gephi units (layer gap 620). Scale them so that neighbouring nodes
     sit about 1.2 node-diameters apart on screen; the inverse is applied when saving. */
  function computePositionScale(b) {
    if (!hasPositions(b)) return 1;
    const nearest = [];
    for (let i = 0; i < b.nodes.length; i++) {
      let best = Infinity;
      for (let j = 0; j < b.nodes.length; j++) {
        if (i === j) continue;
        const d = Math.hypot(b.nodes[i].x - b.nodes[j].x, b.nodes[i].y - b.nodes[j].y);
        if (d > 0 && d < best) best = d;
      }
      if (isFinite(best)) nearest.push(best);
    }
    nearest.sort((p, q) => p - q);
    const median = nearest[Math.floor(nearest.length / 2)] || 1;
    const maxDiameter = Math.max(...b.nodes.map((n) => diameterFor(n.size))) / (viewer.opts.nodeScale || 1);
    return clamp((maxDiameter * 1.2) / median, 0.02, 50);
  }

  function buildCy() {
    if (viewer.cy) { viewer.cy.destroy(); viewer.cy = null; }
    const b = viewer.bundle;
    computeScales();
    viewer.posScale = computePositionScale(b);
    const elements = [];
    for (const n of b.nodes) {
      elements.push({ group: 'nodes', data: { id: n.id, label: n.label, baseLabel: n.base_label, size: n.size, color: n.color, borderColor: n.border_color || '#ffffff', borderWidth: n.border_width || 1, diameter: diameterFor(n.size), metrics: n.metrics || {} }, position: { x: n.x * viewer.posScale, y: n.y * viewer.posScale } });
    }
    for (const e of b.edges) {
      elements.push({ group: 'edges', data: { id: e.id, source: e.source, target: e.target, weight: e.weight, frequency: e.frequency, color: e.color, width: widthFor(e.weight), weightLabel: edgeLabelFor(e), metrics: e.metrics || {} } });
    }
    const cy = cytoscape({
      container: $('#cy'), elements, style: makeStyle(), layout: { name: 'preset', fit: true, padding: 40 },
      boxSelectionEnabled: true, selectionType: 'single', wheelSensitivity: 0.2, minZoom: 0.05, maxZoom: 8, pixelRatio: 'auto',
    });
    viewer.cy = cy;
    $('#cy').style.background = viewer.opts.bg;
    cy.on('select unselect', () => updateSelectionInfo());
    cy.on('tap', 'node, edge', (ev) => showDetails(ev.target));
    if (!hasPositions(b)) cy.layout({ name: 'breadthfirst', directed: true, spacingFactor: 1.15, fit: true, padding: 40, animate: false }).run();
    drawLegend();
  }

  function refreshVisuals() {
    if (!viewer.cy) return;
    viewer.cy.batch(() => {
      viewer.cy.nodes().forEach((n) => n.data('diameter', diameterFor(n.data('size'))));
      viewer.cy.edges().forEach((e) => { if (e.data('widthOverride') == null) e.data('width', widthFor(e.data('weight'))); });
    });
    viewer.cy.style(makeStyle());
    $('#cy').style.background = viewer.opts.bg;
    drawLegend();
  }

  function renderGraphInfo() {
    const b = viewer.bundle;
    const files = Object.entries(b.files || {}).map(([k, p]) => `<a href="/api/artifacts/file?${datasetParam()}&path=${encodeURIComponent(p)}&download=1">${esc(k)}</a>`).join(' · ');
    $('#graphInfo').innerHTML = `<b>${esc(b.name)}</b><br>${esc(b.kind_title || b.kind)}${b.group ? ` · group ${esc(b.group)}` : ''}${b.day ? ` · day ${esc(b.day)}` : ''}<br>${b.counts.nodes} nodes · ${b.counts.edges} edges<br>${b.description ? esc(b.description) + '<br>' : ''}${files ? 'Files: ' + files : ''}`;
    $('#lockedNote').hidden = !b.locked_colors;
    $('#colorControls').hidden = !!b.locked_colors;
  }

  function updateSelectionInfo() {
    if (!viewer.cy) { $('#selectionInfo').textContent = 'Nothing selected.'; return; }
    const n = viewer.cy.nodes(':selected').length, e = viewer.cy.edges(':selected').length;
    $('#selectionInfo').textContent = (n || e) ? `${n} node(s), ${e} edge(s) selected` : 'Nothing selected - click an element, shift+click to add, shift+drag to box-select.';
  }

  function showDetails(ele) {
    const d = ele.data();
    const rows = [];
    const add = (k, v) => { if (v !== null && v !== undefined && v !== '') rows.push([k, v]); };
    if (ele.isNode()) {
      add('Node', d.id); add('Label', d.label); add('Size (Gephi)', fmtNum(d.size, 1)); add('Colour', d.color);
      const m = d.metrics || {};
      if (m.baseline_value != null) add(viewer.bundle.kind === 'day' ? 'Group baseline (avg/day)' : (viewer.bundle.kind === 'differential' ? 'Overall baseline (avg/day)' : 'Average duration per day'), `${fmtDuration(m.baseline_value)} (${fmtNum(m.baseline_value, 0)} s)`);
      if (m.day_value != null) add(viewer.bundle.kind === 'day' ? 'This day' : 'Group baseline (avg/day)', `${fmtDuration(m.day_value)} (${fmtNum(m.day_value, 0)} s)`);
      if (m.difference_value != null) add('Difference', `${m.difference_value > 0 ? '+' : ''}${fmtNum(m.difference_value / 60, 1)} min`);
      if (m.difference_ratio != null) add('Relative difference', `${fmtNum(m.difference_ratio * 100, 1)} %`);
      add('Position (file units)', `${fmtNum(ele.position('x') / (viewer.posScale || 1), 0)}, ${fmtNum(ele.position('y') / (viewer.posScale || 1), 0)}`);
    } else {
      add('Edge', `${d.source} → ${d.target}`); add('Weight (Gephi)', fmtNum(d.weight, 4)); add('Transitions in the log', fmtNum(d.frequency, 0)); add('Colour', d.color);
      const m = d.metrics || {};
      if (m.baseline_value != null) add('Baseline (avg/day)', fmtNum(m.baseline_value, 3));
      if (m.day_value != null) add(viewer.bundle.kind === 'day' ? 'This day' : 'Group (avg/day)', fmtNum(m.day_value, 3));
      if (m.difference_value != null) add('Difference', `${m.difference_value > 0 ? '+' : ''}${fmtNum(m.difference_value, 3)} /day`);
    }
    const table = el('table', { class: 'details-table' });
    for (const [k, v] of rows) table.appendChild(el('tr', {}, [el('td', { text: k }), el('td', { text: String(v) })]));
    const box = $('#elementDetails');
    box.className = '';
    box.innerHTML = '';
    box.appendChild(table);
  }

  function applyNodeColor(hex) {
    if (!viewer.cy) return;
    if (viewer.bundle.locked_colors) { toast('The colours of differential maps are derived from the difference values and cannot be changed.', 'error'); return; }
    const sel = viewer.cy.nodes(':selected');
    if (!sel.length) { toast('Select one or more nodes first (click, shift+click or shift+drag).', 'error'); return; }
    sel.forEach((n) => { n.data('color', hex); n.data('borderColor', hex); viewer.edits.node_colors[n.id()] = hex; });
    drawLegend();
  }
  function applyEdgeColor(hex) {
    if (!viewer.cy) return;
    if (viewer.bundle.locked_colors) { toast('The colours of differential maps are derived from the difference values and cannot be changed.', 'error'); return; }
    const sel = viewer.cy.edges(':selected');
    if (!sel.length) { toast('Select one or more edges first.', 'error'); return; }
    sel.forEach((e) => { e.data('color', hex); viewer.edits.edge_colors[e.id()] = hex; });
  }
  function applyEdgeWidth(px) {
    if (!viewer.cy) return;
    const sel = viewer.cy.edges(':selected');
    if (!sel.length) { toast('Select one or more edges first.', 'error'); return; }
    sel.forEach((e) => { e.data('widthOverride', px); e.data('width', px); viewer.edits.edge_weights[e.id()] = weightForWidth(px); });
  }

  function applyLayout() {
    if (!viewer.cy) return;
    const name = $('#layoutSelect').value;
    const cy = viewer.cy;
    let options = { name, fit: true, padding: 40, animate: false };
    if (name === 'preset') {
      const pos = {}; for (const n of viewer.bundle.nodes) pos[n.id] = { x: n.x * viewer.posScale, y: n.y * viewer.posScale };
      options = { name: 'preset', positions: pos, fit: true, padding: 40 };
    } else if (name === 'breadthfirst') {
      const roots = cy.nodes().filter((n) => n.indegree(false) === 0);
      options = { name, directed: true, roots: roots.length ? roots : undefined, spacingFactor: 1.15, fit: true, padding: 40, animate: false };
    } else if (name === 'cose') {
      options = { name, animate: false, fit: true, padding: 40, nodeRepulsion: () => 400000, idealEdgeLength: () => 160, nodeOverlap: 20, gravity: 0.6, numIter: 1500 };
    } else if (name === 'concentric') {
      options = { name, fit: true, padding: 40, concentric: (n) => n.degree(false), levelWidth: () => 2, minNodeSpacing: 40, animate: false };
    }
    cy.layout(options).run();
  }

  // ---------------------------------------------------------------- legend
  function legendSpec() {
    const b = viewer.bundle;
    const spec = clone(b.legend || {});
    if (!b.locked_colors && viewer.cy) {
      const byColor = new Map();
      viewer.cy.nodes().forEach((n) => {
        if (startEnd().includes(n.id()) || [b.nodes.find((x) => x.id === n.id())].some((x) => x && /start|end/i.test(x.id) && x.metrics && Object.keys(x.metrics).length === 0)) return;
        const c = String(n.data('color')).toLowerCase();
        if (!byColor.has(c)) byColor.set(c, []);
        byColor.get(c).push(n.data('baseLabel') || n.id());
      });
      const entries = Array.from(byColor.entries()).map(([color, names]) => ({ color, label: names.sort().join(', ') })).sort((a, b2) => b2.label.length - a.label.length);
      const se = b.nodes.filter((n) => startEnd().includes(n.id));
      if (se.length) entries.push({ color: se[0].color, label: se.map((n) => n.id).join(' / ') });
      spec.node_color = { type: 'categorical', title: (spec.node_color && spec.node_color.title) || 'Node colour', entries };
    }
    return spec;
  }

  function wrapText(ctx, text, maxWidth) {
    const words = String(text).split(' ');
    const lines = []; let line = '';
    for (const w of words) {
      const test = line ? line + ' ' + w : w;
      if (ctx.measureText(test).width > maxWidth && line) { lines.push(line); line = w; } else line = test;
    }
    if (line) lines.push(line);
    return lines;
  }

  function formatLegendValue(v, item) {
    if (item.format === 'duration') return fmtDuration(v) || '0m';
    return `${fmtNum(v, Math.abs(v) < 10 ? 2 : 0)}${item.unit ? ' ' + item.unit : ''}`;
  }

  /* Draws the legend with the Canvas 2D API; returns its size. Used both on screen and in the exported PNG. */
  function drawLegendInto(ctx, spec, x0, y0, scale, measureOnly) {
    const s = scale || 1;
    const pad = 10 * s, lineH = 15 * s, width = 300 * s;
    ctx.save();
    ctx.font = `${11 * s}px "Segoe UI", Roboto, Helvetica, Arial, sans-serif`;
    ctx.textBaseline = 'middle';
    const blocks = [];
    if (spec.node_color) blocks.push(spec.node_color);
    if (spec.edge_color) blocks.push(spec.edge_color);
    if (spec.edge_width) blocks.push({ ...spec.edge_width, kind: 'width' });
    if (spec.node_size) blocks.push({ ...spec.node_size, kind: 'size' });
    // measure
    let height = pad;
    const layout = [];
    for (const blk of blocks) {
      ctx.font = `bold ${11 * s}px "Segoe UI", Roboto, Helvetica, Arial, sans-serif`;
      const titleLines = wrapText(ctx, blk.title || '', width - 2 * pad);
      ctx.font = `${11 * s}px "Segoe UI", Roboto, Helvetica, Arial, sans-serif`;
      let h = titleLines.length * lineH + 2 * s;
      const rows = [];
      if (blk.type === 'categorical') {
        for (const e of blk.entries || []) { const lines = wrapText(ctx, e.label, width - 2 * pad - 22 * s); rows.push({ color: e.color, lines }); h += lines.length * lineH; }
      } else if (blk.type === 'diverging') h += 30 * s;
      else if (blk.kind === 'width') h += 30 * s;
      else if (blk.kind === 'size') h += 40 * s;
      layout.push({ blk, titleLines, rows, h });
      height += h + 6 * s;
    }
    height += pad - 6 * s;
    if (measureOnly) { ctx.restore(); return { width, height }; }
    // background
    ctx.fillStyle = 'rgba(255,255,255,0.94)'; ctx.strokeStyle = '#9ca3af'; ctx.lineWidth = 1 * s;
    ctx.beginPath(); ctx.rect(x0, y0, width, height); ctx.fill(); ctx.stroke();
    let y = y0 + pad;
    for (const item of layout) {
      const blk = item.blk;
      ctx.fillStyle = '#111827';
      ctx.font = `bold ${11 * s}px "Segoe UI", Roboto, Helvetica, Arial, sans-serif`;
      for (const line of item.titleLines) { ctx.fillText(line, x0 + pad, y + lineH / 2); y += lineH; }
      y += 2 * s;
      ctx.font = `${11 * s}px "Segoe UI", Roboto, Helvetica, Arial, sans-serif`;
      if (blk.type === 'categorical') {
        for (const row of item.rows) {
          ctx.beginPath(); ctx.arc(x0 + pad + 7 * s, y + lineH / 2, 6 * s, 0, Math.PI * 2); ctx.fillStyle = row.color; ctx.fill(); ctx.strokeStyle = 'rgba(0,0,0,0.35)'; ctx.stroke();
          ctx.fillStyle = '#111827';
          for (const line of row.lines) { ctx.fillText(line, x0 + pad + 20 * s, y + lineH / 2); y += lineH; }
        }
      } else if (blk.type === 'diverging') {
        const bx = x0 + pad, bw = width - 2 * pad, bh = 12 * s;
        const grad = ctx.createLinearGradient(bx, 0, bx + bw, 0);
        const cols = blk.colors || ['#ff0000', '#a3a3a3', '#006400'];
        grad.addColorStop(0, cols[0]); grad.addColorStop(0.5, cols[1]); grad.addColorStop(1, cols[2]);
        ctx.fillStyle = grad; ctx.fillRect(bx, y + 2 * s, bw, bh); ctx.strokeStyle = '#6b7280'; ctx.strokeRect(bx, y + 2 * s, bw, bh);
        ctx.fillStyle = '#111827'; ctx.textAlign = 'left'; ctx.fillText(formatLegendValue(blk.min, blk), bx, y + bh + 11 * s);
        ctx.textAlign = 'center'; ctx.fillText(`0${blk.unit ? ' ' + blk.unit : ''}`, bx + bw / 2, y + bh + 11 * s);
        ctx.textAlign = 'right'; ctx.fillText(formatLegendValue(blk.max, blk), bx + bw, y + bh + 11 * s);
        ctx.textAlign = 'left';
        y += 30 * s;
      } else if (blk.kind === 'width') {
        const bx = x0 + pad, bw = width - 2 * pad;
        ctx.fillStyle = '#4b5563';
        ctx.beginPath(); ctx.moveTo(bx, y + 8 * s); ctx.lineTo(bx + bw, y + 2 * s); ctx.lineTo(bx + bw, y + 14 * s); ctx.lineTo(bx, y + 9 * s); ctx.closePath(); ctx.fill();
        ctx.fillStyle = '#111827'; ctx.textAlign = 'left'; ctx.fillText(formatLegendValue(blk.min, blk), bx, y + 24 * s);
        ctx.textAlign = 'right'; ctx.fillText(formatLegendValue(blk.max, blk), bx + bw, y + 24 * s); ctx.textAlign = 'left';
        y += 30 * s;
      } else if (blk.kind === 'size') {
        const bx = x0 + pad;
        ctx.fillStyle = '#d1d5db'; ctx.strokeStyle = '#6b7280';
        ctx.beginPath(); ctx.arc(bx + 8 * s, y + 18 * s, 6 * s, 0, Math.PI * 2); ctx.fill(); ctx.stroke();
        ctx.beginPath(); ctx.arc(bx + 40 * s, y + 18 * s, 16 * s, 0, Math.PI * 2); ctx.fill(); ctx.stroke();
        ctx.fillStyle = '#111827'; ctx.fillText(`${formatLegendValue(blk.min, blk)} … ${formatLegendValue(blk.max, blk)}`, bx + 64 * s, y + 18 * s);
        y += 40 * s;
      }
      y += 6 * s;
    }
    ctx.restore();
    return { width, height };
  }

  function drawLegend() {
    const canvas = $('#legendCanvas');
    if (!viewer.cy || !viewer.opts.legend) { canvas.hidden = true; return; }
    canvas.hidden = false;
    const spec = legendSpec();
    const dpr = window.devicePixelRatio || 1;
    const probe = canvas.getContext('2d');
    const size = drawLegendInto(probe, spec, 0, 0, 1, true);
    canvas.width = Math.ceil(size.width * dpr) + 2; canvas.height = Math.ceil(size.height * dpr) + 2;
    canvas.style.width = `${size.width + 2}px`; canvas.style.height = `${size.height + 2}px`;
    const ctx = canvas.getContext('2d');
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    ctx.clearRect(0, 0, canvas.width, canvas.height);
    drawLegendInto(ctx, spec, 1, 1, 1, false);
  }

  async function saveFigure() {
    if (!viewer.cy) { toast('Open a graph first.', 'error'); return; }
    try {
      const scale = 2;
      const blob = await viewer.cy.png({ output: 'blob-promise', full: true, scale, bg: viewer.opts.bg, maxWidth: 6000, maxHeight: 6000 });
      const img = await new Promise((resolve, reject) => { const i = new Image(); i.onload = () => resolve(i); i.onerror = reject; i.src = URL.createObjectURL(blob); });
      const spec = legendSpec();
      const legendScale = scale * clamp(img.width / 2400, 1, 3);  // keep the legend readable on very large maps
      const pad = 12 * legendScale;
      const canvas = document.createElement('canvas');
      const probe = canvas.getContext('2d');
      const lsize = viewer.opts.legend ? drawLegendInto(probe, spec, 0, 0, legendScale, true) : { width: 0, height: 0 };
      canvas.width = Math.max(img.width, lsize.width + 2 * pad);
      canvas.height = img.height + (viewer.opts.legend ? lsize.height + 2 * pad : 0);
      const ctx = canvas.getContext('2d');
      ctx.fillStyle = viewer.opts.bg; ctx.fillRect(0, 0, canvas.width, canvas.height);
      ctx.drawImage(img, 0, 0);
      if (viewer.opts.legend) drawLegendInto(ctx, spec, pad, img.height + pad, legendScale, false);
      URL.revokeObjectURL(img.src);
      const dataUrl = canvas.toDataURL('image/png');
      const name = viewer.graphId.replace(/[\\/]+/g, '__');
      const a = el('a', { href: dataUrl, download: `${name}.png` });
      document.body.appendChild(a); a.click(); a.remove();
      const res = await api.post(`/api/figures?${datasetParam()}`, { graph_id: viewer.graphId, name, image: dataUrl });
      toast(`Figure downloaded and saved to ${res.path}`, 'ok');
      loadArtifacts();
    } catch (e) { toast(`Could not save the figure: ${e.message}`, 'error'); }
  }

  async function saveGraph() {
    if (!viewer.cy) { toast('Open a graph first.', 'error'); return; }
    const s = viewer.posScale || 1;
    viewer.cy.nodes().forEach((n) => { viewer.edits.positions[n.id()] = { x: n.position('x') / s, y: n.position('y') / s }; });
    try {
      const res = await api.post(`/api/graphs/${viewer.graphId.split('/').map(encodeURIComponent).join('/')}/save?${datasetParam()}`, { edits: viewer.edits });
      toast(`Saved: ${Object.values(res.files).join(', ')}`, 'ok');
      await loadGraphList();
      loadArtifacts();
    } catch (e) { toast(e.message, 'error'); }
  }

  // ================================================================== events
  function bindEvents() {
    $$('.tab').forEach((t) => t.addEventListener('click', () => showPage(t.dataset.page)));
    window.addEventListener('hashchange', () => showPage((location.hash || '#data').slice(1)));
    $('#btnToggleLog').addEventListener('click', () => openLog());
    $('#btnCloseLog').addEventListener('click', () => openLog(false));
    $('#btnClearLog').addEventListener('click', async () => { $('#logBody').innerHTML = ''; try { await api.post('/api/logs/clear'); } catch (e) { /* ignore */ } });
    $('#btnToggleCredits').addEventListener('click', () => { const panel = $('#creditsPanel'); panel.hidden = !panel.hidden; });
    $('#btnCloseCredits').addEventListener('click', () => { $('#creditsPanel').hidden = true; });
    $('#btnGoStyle').addEventListener('click', () => { showPage('style'); window.scrollTo(0, 0); });
    $('#btnGoViewer').addEventListener('click', () => { showPage('viewer'); window.scrollTo(0, 0); });
    $('#btnNodesShowAll').addEventListener('click', () => { hiddenNodes().length = 0; renderNodeColorList(); renderNodeLabelList(); });

    // page 1
    $('#useDefaultDataset').addEventListener('change', (ev) => { if (ev.target.checked) loadDataset({ source: 'default' }); });
    $('#btnLoadPath').addEventListener('click', () => { const p = $('#datasetPath').value.trim(); if (!p) { toast('Enter the path of the file.', 'error'); return; } $('#useDefaultDataset').checked = false; loadDataset({ source: 'path', path: p }); });
    $('#datasetPath').addEventListener('keydown', (ev) => { if (ev.key === 'Enter') $('#btnLoadPath').click(); });
    $('#btnUpload').addEventListener('click', () => { const f = $('#datasetUpload').files[0]; if (!f) { toast('Choose a file to upload.', 'error'); return; } const form = new FormData(); form.append('file', f); $('#useDefaultDataset').checked = false; loadDataset(null, form); });
    $('#btnColsAll').addEventListener('click', () => { $$('#columnsList input').forEach((i) => { i.checked = true; }); updateColsCount(); });
    $('#btnColsNone').addEventListener('click', () => { $$('#columnsList input').forEach((i) => { i.checked = requiredColumns().has(i.value); }); updateColsCount(); });
    $('#btnColsDefault').addEventListener('click', () => renderColumns(state.defaults.data.columns_to_keep));
    for (const id of ['#colActivity', '#colTimestamp', '#colDay', '#colCase', '#colDuration', '#colStart', '#colEnd']) $(id).addEventListener('change', () => { collectPage1(); renderColumns($$('#columnsList input:checked').map((i) => i.value)); });
    $('#groupColumn').addEventListener('change', () => { state.settings.data.group_column = $('#groupColumn').value || null; state.settings.data.group_values = null; renderGroupValues(); renderColumns($$('#columnsList input:checked').map((i) => i.value)); });
    $('#filterColumn').addEventListener('change', renderFilterValues);
    $('#btnAddFilter').addEventListener('click', () => {
      const column = $('#filterColumn').value; const values = $$('#filterValues input:checked').map((i) => i.value);
      if (!column || !values.length) { toast('Choose a column and tick at least one value.', 'error'); return; }
      state.settings.data.filters = state.settings.data.filters || [];
      state.settings.data.filters.push({ column, mode: $('#filterMode').value, values });
      renderFilters();
    });
    $('#btnDiscover').addEventListener('click', discover);
    $('#btnSaveDefaults1').addEventListener('click', saveDefaults);
    $('#btnResetDefaults').addEventListener('click', resetDefaults);
    $('#artifactDataset').addEventListener('change', () => { state.artifactDataset = $('#artifactDataset').value; state.activeArtifact = null; loadArtifacts(); loadGraphList(); });
    $('#artifactFilter').addEventListener('input', renderArtifactList);
    $('#btnRefreshArtifacts').addEventListener('click', () => { loadArtifacts(); loadGraphList(); });

    // page 2
    $('#nodeFilter').addEventListener('input', renderNodeColorList);
    $('#btnNodesAll').addEventListener('click', () => { state.nodes.forEach((n) => state.nodeSelection.add(n.id)); renderNodeColorList(); });
    $('#btnNodesNone').addEventListener('click', () => { state.nodeSelection.clear(); renderNodeColorList(); });
    $('#btnNodesFiltered').addEventListener('click', () => { $$('#nodeColorList .node-row').forEach((r) => state.nodeSelection.add(r.dataset.id)); renderNodeColorList(); });
    $('#btnApplyCustomColor').addEventListener('click', () => assignColorToSelection($('#customColor').value));
    $('#btnResetColors').addEventListener('click', () => { state.settings.style.node_colors = clone(state.defaults.style.node_colors); renderNodeColorList(); renderColorRules(); });
    $('#btnResetLabels').addEventListener('click', () => { state.settings.style.node_labels = clone(state.defaults.style.node_labels || {}); renderNodeLabelList(); });
    $('#noThreshold').addEventListener('change', () => { $('#edgeThreshold').disabled = $('#noThreshold').checked; });
    $('#btnBuild').addEventListener('click', buildGraphs);
    $('#btnSaveDefaults2').addEventListener('click', saveDefaults);

    // page 3
    $('#graphSelect').addEventListener('change', () => openGraph($('#graphSelect').value));
    $('#btnReloadGraphs').addEventListener('click', loadGraphList);
    $('#btnApplyLayout').addEventListener('click', applyLayout);
    $('#btnFit').addEventListener('click', () => viewer.cy && viewer.cy.fit(undefined, 40));
    $('#showLegend').addEventListener('change', (ev) => { viewer.opts.legend = ev.target.checked; drawLegend(); });
    $('#showEdgeLabels').addEventListener('change', (ev) => { viewer.opts.edgeLabels = ev.target.checked; refreshVisuals(); });
    $('#showArrows').addEventListener('change', (ev) => { viewer.opts.arrows = ev.target.checked; refreshVisuals(); });
    $('#curvedEdges').addEventListener('change', (ev) => { viewer.opts.curved = ev.target.checked; refreshVisuals(); });
    $('#labelPosition').addEventListener('change', (ev) => { viewer.opts.labelPosition = ev.target.value; refreshVisuals(); });
    $('#bgColor').addEventListener('input', (ev) => { viewer.opts.bg = ev.target.value; refreshVisuals(); });
    $('#nodeScale').addEventListener('input', (ev) => { viewer.opts.nodeScale = parseFloat(ev.target.value); $('#nodeScaleVal').textContent = ev.target.value; refreshVisuals(); });
    $('#edgeScale').addEventListener('input', (ev) => { viewer.opts.edgeScale = parseFloat(ev.target.value); $('#edgeScaleVal').textContent = ev.target.value; refreshVisuals(); });
    $('#fontSize').addEventListener('input', (ev) => { viewer.opts.fontSize = parseInt(ev.target.value, 10); $('#fontSizeVal').textContent = ev.target.value; refreshVisuals(); });
    $('#btnViewerApplyNodeColor').addEventListener('click', () => applyNodeColor($('#viewerCustomColor').value));
    $('#btnViewerApplyEdgeColor').addEventListener('click', () => applyEdgeColor($('#viewerEdgeColor').value));
    $('#btnViewerApplyEdgeWidth').addEventListener('click', () => applyEdgeWidth(parseFloat($('#viewerEdgeWidth').value) || 3));
    $('#btnSelectAllEdges').addEventListener('click', () => { if (viewer.cy) { viewer.cy.elements().unselect(); viewer.cy.edges().select(); } });
    $('#btnSelectAllNodes').addEventListener('click', () => { if (viewer.cy) { viewer.cy.elements().unselect(); viewer.cy.nodes().select(); } });
    $('#btnResetGraph').addEventListener('click', () => { if (viewer.graphId) openGraph(viewer.graphId); });
    $('#btnSaveFigure').addEventListener('click', saveFigure);
    $('#btnSaveGraph').addEventListener('click', saveGraph);
    window.addEventListener('resize', () => { if (viewer.cy) { viewer.cy.resize(); drawLegend(); } });
  }

  document.addEventListener('DOMContentLoaded', () => { init().catch((e) => { console.error(e); toast(`Could not initialise the dashboard: ${e.message}`, 'error'); }); });
  window.ContextPM = { state, viewer };  // exposed for debugging / automated tests
})();
