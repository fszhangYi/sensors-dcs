/**
 * Infer Tab · Flow editor (P0): toolbox, nodes, edges, inspector, localStorage.
 * Runtime hooks: window.__flowEditor / window.FlowRuntime
 */
(function () {
  'use strict';

  const LS_KEY = 'dcs.inf.flow.graph';
  const NODE_W = 168;
  const LINK_SNAP_PX = 140;

  function t(key, vars) {
    try {
      if (typeof window.t === 'function') return window.t(key, vars);
    } catch (_) {}
    return key;
  }

  function uid(prefix) {
    return (prefix || 'id') + '_' + Math.random().toString(36).slice(2, 10);
  }

  function defaultPose7() {
    return { xyzrpy: [0, 0, 0, 0, 0, 0], gripper: 0 };
  }

  function defaultTiming() {
    return {
      t_min_s: 0.1,
      t_max_s: 30,
      v_norm_rad_s: 0.02,
      jerk_seg_frac: 0.10,
      accel_seg_frac: 0.15,
    };
  }

  function defaultTol() {
    return { pos_m: 0.01, rot_rad: 0.05, grip: 0.05 };
  }

  function defaultTermCond() {
    return { direction: 'z_rise', threshold: null };
  }

  /** Normalize legacy {z_rise_to,z_fall_to} → {direction,threshold}. */
  function normalizeTermCond(tc) {
    if (!tc || typeof tc !== 'object') return defaultTermCond();
    if (tc.direction === 'z_rise' || tc.direction === 'z_fall') {
      const th = tc.threshold;
      return {
        direction: tc.direction,
        threshold: (th != null && Number.isFinite(Number(th))) ? Number(th) : null,
      };
    }
    if (tc.z_rise_to != null && Number.isFinite(Number(tc.z_rise_to))) {
      return { direction: 'z_rise', threshold: Number(tc.z_rise_to) };
    }
    if (tc.z_fall_to != null && Number.isFinite(Number(tc.z_fall_to))) {
      return { direction: 'z_fall', threshold: Number(tc.z_fall_to) };
    }
    return defaultTermCond();
  }

  function createBasicModule(x, y) {
    return {
      id: uid('m'),
      type: 'basic',
      title: '',
      x: x || 80,
      y: y || 80,
      start: defaultPose7(),
      goal: defaultPose7(),
      motion_mode: 'program',
      term_cond: defaultTermCond(),
      start_tol: defaultTol(),
      timing: defaultTiming(),
    };
  }

  /** Pose-check: program-only, goal-only (move from current pose → goal). */
  function createPoseCheckModule(x, y) {
    return {
      id: uid('m'),
      type: 'pose_check',
      title: '',
      x: x || 80,
      y: y || 80,
      goal: defaultPose7(),
      motion_mode: 'program',
      timing: defaultTiming(),
    };
  }

  function createModule(type, x, y) {
    if (type === 'pose_check') return createPoseCheckModule(x, y);
    return createBasicModule(x, y);
  }

  function emptyGraph() {
    return { version: 1, modules: [], edges: [], meta: { name: '', updated_at: '' } };
  }

  function migrateModule(m) {
    if (!m || typeof m !== 'object') return null;
    const type = m.type === 'pose_check' ? 'pose_check' : 'basic';
    if (type === 'pose_check') {
      return {
        id: m.id || uid('m'),
        type: 'pose_check',
        title: m.title || '',
        x: Number(m.x) || 80,
        y: Number(m.y) || 80,
        goal: m.goal || defaultPose7(),
        motion_mode: 'program',
        timing: m.timing || defaultTiming(),
      };
    }
    return {
      id: m.id || uid('m'),
      type: 'basic',
      title: m.title || '',
      x: Number(m.x) || 80,
      y: Number(m.y) || 80,
      start: m.start || defaultPose7(),
      goal: m.goal || defaultPose7(),
      motion_mode: m.motion_mode === 'infer' ? 'infer' : 'program',
      term_cond: normalizeTermCond(m.term_cond),
      start_tol: m.start_tol || defaultTol(),
      timing: m.timing || defaultTiming(),
    };
  }

  function loadGraph() {
    try {
      const raw = localStorage.getItem(LS_KEY);
      if (!raw) return emptyGraph();
      const g = JSON.parse(raw);
      if (!g || !Array.isArray(g.modules) || !Array.isArray(g.edges)) return emptyGraph();
      g.version = 1;
      g.modules = g.modules.map(migrateModule).filter(Boolean);
      return g;
    } catch (_) {
      return emptyGraph();
    }
  }

  function saveGraph(g) {
    try {
      g.meta = g.meta || {};
      g.meta.updated_at = new Date().toISOString();
      localStorage.setItem(LS_KEY, JSON.stringify(g));
    } catch (_) {}
  }

  /* —— topology (mirrors flow_graph.py) —— */
  function findEntryIds(modules, edges) {
    const ids = new Set(modules.map((m) => String(m.id)));
    const incoming = new Set();
    edges.forEach((e) => {
      const to = String(e.to || '');
      if (ids.has(to)) incoming.add(to);
    });
    return [...ids].filter((id) => !incoming.has(id)).sort();
  }

  function hasCycle(modules, edges) {
    const ids = new Set(modules.map((m) => String(m.id)));
    const adj = {};
    ids.forEach((id) => { adj[id] = []; });
    edges.forEach((e) => {
      const frm = String(e.from || '');
      const to = String(e.to || '');
      if (adj[frm] && ids.has(to)) adj[frm].push(to);
    });
    const visiting = new Set();
    const done = new Set();
    function dfs(n) {
      if (done.has(n)) return false;
      if (visiting.has(n)) return true;
      visiting.add(n);
      for (const nxt of adj[n]) {
        if (dfs(nxt)) return true;
      }
      visiting.delete(n);
      done.add(n);
      return false;
    }
    for (const n of ids) {
      if (dfs(n)) return true;
    }
    return false;
  }

  function outDegrees(modules, edges) {
    const deg = {};
    modules.forEach((m) => { deg[String(m.id)] = 0; });
    edges.forEach((e) => {
      const frm = String(e.from || '');
      if (frm in deg) deg[frm] += 1;
    });
    return deg;
  }

  function validateChain(modules, edges) {
    if (!modules.length) return { ok: false, entry: null, error: 'empty_graph' };
    if (hasCycle(modules, edges)) return { ok: false, entry: null, error: 'cycle' };
    const deg = outDegrees(modules, edges);
    for (const mid of Object.keys(deg)) {
      if (deg[mid] > 1) return { ok: false, entry: mid, error: 'out_degree_gt1' };
    }
    const entries = findEntryIds(modules, edges);
    if (entries.length !== 1) return { ok: false, entry: null, error: 'need_single_entry' };
    return { ok: true, entry: entries[0], error: null };
  }

  function fmtPoseShort(p) {
    const a = (p && p.xyzrpy) || [0, 0, 0, 0, 0, 0];
    const g = (p && p.gripper != null) ? Number(p.gripper) : 0;
    return a.slice(0, 3).map((v) => Number(v).toFixed(2)).join(',') + ' ·g' + Number(g).toFixed(2);
  }

  function parsePoseCsv(text) {
    const normalized = String(text || '')
      .trim()
      .replace(/[［\[]/g, '')
      .replace(/[］\]]/g, '')
      .replace(/，/g, ',')
      .replace(/；/g, ';')
      .replace(/[|／/]/g, ',');
    const parts = normalized.split(/[,\s;]+/).filter((p) => p !== '').map(Number);
    if (parts.length < 6 || parts.some((v) => !Number.isFinite(v))) return null;
    const grip = parts.length >= 7 && Number.isFinite(parts[6]) ? parts[6] : 0;
    return { xyzrpy: parts.slice(0, 6), gripper: grip };
  }

  function poseToCsv(p) {
    const a = (p && p.xyzrpy) || [0, 0, 0, 0, 0, 0];
    const g = (p && p.gripper != null) ? p.gripper : 0;
    return a.map((v) => Number(v).toFixed(4)).join(',') + ',' + Number(g).toFixed(3);
  }

  function escapeAttr(s) {
    return String(s || '')
      .replace(/&/g, '&amp;')
      .replace(/"/g, '&quot;')
      .replace(/</g, '&lt;')
      .replace(/>/g, '&gt;');
  }

  function poseFieldsHtml(idPrefix, pose, label, syncBtnId) {
    const a = (pose && pose.xyzrpy) || [0, 0, 0, 0, 0, 0];
    const g = (pose && pose.gripper != null) ? pose.gripper : 0;
    const keys = ['x', 'y', 'z', 'rx', 'ry', 'rz', 'g'];
    const vals = a.slice(0, 6).concat([g]);
    let head = '<div class="flow-field-head"><label>' + label + '</label>';
    if (syncBtnId) {
      head += '<button type="button" class="flow-sync-btn" id="' + syncBtnId + '">' + t('infer.flow_sync') + '</button>';
    }
    head += '</div>';
    let grid = '<div class="flow-pose-grid">';
    for (let i = 0; i < 7; i++) {
      grid += '<label class="flow-pose-cell">' + keys[i] +
        '<input type="number" step="any" id="' + idPrefix + keys[i] + '" value="' +
        (Number.isFinite(Number(vals[i])) ? vals[i] : 0) + '" /></label>';
    }
    grid += '</div>';
    return '<div class="flow-field">' + head + grid + '</div>';
  }

  function poseCsvFieldHtml(textareaId, pose, label, syncBtnId) {
    let head = '<div class="flow-field-head"><label>' + label + ' (x,y,z,rx,ry,rz,g)</label>';
    if (syncBtnId) {
      head += '<button type="button" class="flow-sync-btn" id="' + syncBtnId + '">' + t('infer.flow_sync') + '</button>';
    }
    head += '</div>';
    return (
      '<div class="flow-field">' + head +
        '<textarea id="' + textareaId + '">' + poseToCsv(pose) + '</textarea></div>'
    );
  }

  function bindSyncPose(box, btnId, onPose) {
    const btn = box.querySelector('#' + btnId);
    if (!btn) return;
    btn.onclick = (ev) => {
      ev.preventDefault();
      ev.stopPropagation();
      const p = readPoseFromLive();
      if (!p) { setHint(t('infer.flow_need_read')); return; }
      onPose(p);
      setHint(t('infer.flow_synced'));
    };
  }

  function readPoseFields(box, idPrefix) {
    const keys = ['x', 'y', 'z', 'rx', 'ry', 'rz', 'g'];
    const vals = keys.map((k) => {
      const el = box.querySelector('#' + idPrefix + k);
      return el ? Number(el.value) : NaN;
    });
    if (vals.some((v) => !Number.isFinite(v))) return null;
    return { xyzrpy: vals.slice(0, 6), gripper: vals[6] };
  }

  function fillPoseFields(box, idPrefix, pose) {
    if (!pose) return;
    const a = pose.xyzrpy || [0, 0, 0, 0, 0, 0];
    const vals = {
      x: a[0], y: a[1], z: a[2], rx: a[3], ry: a[4], rz: a[5], g: pose.gripper || 0,
    };
    Object.keys(vals).forEach((k) => {
      const el = box.querySelector('#' + idPrefix + k);
      if (el) el.value = String(vals[k]);
    });
  }

  const editor = {
    graph: emptyGraph(),
    selectedId: null,
    selectedEdgeId: null,
    pan: { x: 40, y: 40 },
    zoom: 1,
    linking: null,
    moduleStates: {},
    els: {},
  };

  function setHint(msg) {
    if (editor.els.hint) editor.els.hint.textContent = msg || '';
  }

  function moduleById(id) {
    return editor.graph.modules.find((m) => m.id === id) || null;
  }

  function persist() {
    saveGraph(editor.graph);
  }

  function worldPointFromClient(clientX, clientY) {
    const wrap = editor.els.wrap;
    const rect = wrap.getBoundingClientRect();
    return {
      x: (clientX - rect.left - editor.pan.x) / editor.zoom,
      y: (clientY - rect.top - editor.pan.y) / editor.zoom,
    };
  }

  function applyWorldTransform() {
    if (!editor.els.world) return;
    editor.els.world.style.transform =
      'translate(' + editor.pan.x + 'px,' + editor.pan.y + 'px) scale(' + editor.zoom + ')';
  }

  function portCenter(mod, which) {
    // Prefer local layout sizes (untransformed); screen rect + pan/zoom is easy to get wrong.
    const el = editor.els.nodes && editor.els.nodes.querySelector('.flow-node[data-id="' + mod.id + '"]');
    const w = (el && el.offsetWidth) ? el.offsetWidth : NODE_W;
    const h = (el && el.offsetHeight) ? el.offsetHeight : 72;
    return {
      x: Number(mod.x) + (which === 'out' ? w : 0),
      y: Number(mod.y) + h / 2,
    };
  }

  function edgeControls(p0, p1) {
    const dx = Math.max(48, Math.abs(p1.x - p0.x) * 0.5);
    return {
      c1: { x: p0.x + dx, y: p0.y },
      c2: { x: p1.x - dx, y: p1.y },
    };
  }

  function cubicAt(p0, c1, c2, p1, t) {
    const u = 1 - t;
    return {
      x: u * u * u * p0.x + 3 * u * u * t * c1.x + 3 * u * t * t * c2.x + t * t * t * p1.x,
      y: u * u * u * p0.y + 3 * u * u * t * c1.y + 3 * u * t * t * c2.y + t * t * t * p1.y,
    };
  }

  function edgePathD(p0, p1) {
    const { c1, c2 } = edgeControls(p0, p1);
    return {
      d: 'M' + p0.x + ',' + p0.y + ' C' + c1.x + ',' + c1.y + ' ' + c2.x + ',' + c2.y + ' ' + p1.x + ',' + p1.y,
      mid: cubicAt(p0, c1, c2, p1, 0.5),
      c1: c1,
      c2: c2,
    };
  }

  function svgEl(name, attrs) {
    const el = document.createElementNS('http://www.w3.org/2000/svg', name);
    if (attrs) {
      Object.keys(attrs).forEach((k) => {
        if (attrs[k] != null) el.setAttribute(k, String(attrs[k]));
      });
    }
    return el;
  }

  function deleteEdgeById(edgeId) {
    if (!edgeId) return;
    const before = editor.graph.edges.length;
    editor.graph.edges = editor.graph.edges.filter((e) => e.id !== edgeId);
    if (editor.graph.edges.length === before) return;
    if (editor.selectedEdgeId === edgeId) editor.selectedEdgeId = null;
    persist();
    redrawEdges();
    setHint(t('infer.flow_hint_edge_deleted'));
  }

  function selectEdge(edgeId) {
    editor.selectedEdgeId = edgeId || null;
    editor.selectedId = null;
    if (editor.els.nodes) {
      editor.els.nodes.querySelectorAll('.flow-node').forEach((n) => n.classList.remove('selected'));
    }
    redrawEdges();
    renderInspector();
    if (edgeId) setHint(t('infer.flow_hint_edge_selected'));
  }

  function redrawEdges() {
    const svg = editor.els.edges;
    if (!svg) return;
    while (svg.firstChild) svg.removeChild(svg.firstChild);

    const defs = svgEl('defs');
    // Slim chevron arrowheads (userSpaceOnUse = stable size, not chunky stroke-scaled wedges)
    [
      { id: 'flowArrow', fill: '#7eb8ad' },
      { id: 'flowArrowActive', fill: '#3dd6c0' },
      { id: 'flowArrowTemp', fill: '#e6b84d' },
    ].forEach((m) => {
      const marker = svgEl('marker', {
        id: m.id,
        viewBox: '0 0 12 12',
        refX: '10',
        refY: '6',
        markerWidth: '8',
        markerHeight: '8',
        orient: 'auto',
        markerUnits: 'userSpaceOnUse',
      });
      marker.appendChild(svgEl('path', {
        d: 'M2,1.5 L10,6 L2,10.5 L4.2,6 Z',
        fill: m.fill,
      }));
      defs.appendChild(marker);
    });
    svg.appendChild(defs);

    editor.graph.edges.forEach((e) => {
      const a = moduleById(e.from);
      const b = moduleById(e.to);
      if (!a || !b) return;
      const p0 = portCenter(a, 'out');
      const p1 = portCenter(b, 'in');
      const geom = edgePathD(p0, p1);
      const selected = editor.selectedEdgeId === e.id;
      const g = svgEl('g', {
        class: 'flow-edge-group' + (selected ? ' is-selected' : ''),
        'data-edge-id': e.id,
      });

      const hit = svgEl('path', {
        class: 'flow-edge-hit',
        d: geom.d,
        fill: 'none',
        stroke: 'transparent',
        'stroke-width': '16',
      });
      const line = svgEl('path', {
        class: 'flow-edge-line',
        d: geom.d,
        fill: 'none',
        stroke: selected ? '#3dd6c0' : '#7eb8ad',
        'stroke-width': selected ? '2.25' : '1.75',
        'stroke-linecap': 'round',
        'stroke-linejoin': 'round',
        'marker-end': selected ? 'url(#flowArrowActive)' : 'url(#flowArrow)',
        opacity: selected ? '1' : '0.92',
      });

      // Midpoint delete control
      const btn = svgEl('g', {
        class: 'flow-edge-del',
        transform: 'translate(' + geom.mid.x + ',' + geom.mid.y + ')',
      });
      btn.appendChild(svgEl('circle', {
        class: 'flow-edge-del-bg',
        cx: '0',
        cy: '0',
        r: selected ? '9' : '7',
      }));
      btn.appendChild(svgEl('path', {
        class: 'flow-edge-del-x',
        d: 'M-3.2,-3.2 L3.2,3.2 M3.2,-3.2 L-3.2,3.2',
        fill: 'none',
        'stroke-width': '1.6',
        'stroke-linecap': 'round',
      }));

      g.appendChild(hit);
      g.appendChild(line);
      g.appendChild(btn);
      svg.appendChild(g);

      const onSelect = (ev) => {
        ev.preventDefault();
        ev.stopPropagation();
        selectEdge(e.id);
      };
      hit.addEventListener('pointerdown', onSelect);
      line.addEventListener('pointerdown', onSelect);
      btn.addEventListener('pointerdown', (ev) => {
        ev.preventDefault();
        ev.stopPropagation();
        deleteEdgeById(e.id);
      });
      g.addEventListener('dblclick', (ev) => {
        ev.preventDefault();
        ev.stopPropagation();
        deleteEdgeById(e.id);
      });
    });

    if (editor.linking && editor.linking.fromId && editor.linking.cur) {
      const a = moduleById(editor.linking.fromId);
      if (a) {
        const p0 = portCenter(a, 'out');
        const p1 = editor.linking.cur;
        const geom = edgePathD(p0, p1);
        svg.appendChild(svgEl('path', {
          class: 'flow-edge-temp',
          d: geom.d,
          fill: 'none',
          stroke: '#e6b84d',
          'stroke-width': '1.75',
          'stroke-dasharray': '5 5',
          'stroke-linecap': 'round',
          'marker-end': 'url(#flowArrowTemp)',
          opacity: '0.9',
        }));
      }
    }

    svg.setAttribute('width', '4000');
    svg.setAttribute('height', '3000');
    svg.setAttribute('viewBox', '0 0 4000 3000');
    svg.style.width = '4000px';
    svg.style.height = '3000px';
  }

  function stateLabel(st) {
    return st || 'idle';
  }

  function typeLabel(m) {
    if (m.title) return m.title;
    if (m.type === 'pose_check') return t('infer.flow_pose_check');
    return t('infer.flow_basic');
  }

  function renderNodes() {
    const layer = editor.els.nodes;
    if (!layer) return;
    layer.innerHTML = '';
    editor.graph.modules.forEach((m) => {
      const st = (editor.moduleStates[m.id] && editor.moduleStates[m.id].state) || 'idle';
      const err = (editor.moduleStates[m.id] && editor.moduleStates[m.id].error) || '';
      const el = document.createElement('div');
      el.className = 'flow-node' + (m.id === editor.selectedId ? ' selected' : '');
      el.dataset.id = m.id;
      el.dataset.type = m.type || 'basic';
      el.dataset.state = st;
      el.style.left = m.x + 'px';
      el.style.top = m.y + 'px';
      const isCheck = m.type === 'pose_check';
      const modeLab = isCheck
        ? t('infer.flow_mode_program')
        : (m.motion_mode === 'infer' ? t('infer.flow_mode_infer') : t('infer.flow_mode_program'));
      let body = '<div class="flow-node-mode">' + modeLab + '</div>';
      if (isCheck) {
        body +=
          '<div class="flow-node-line"><span class="flow-node-k">' + t('infer.flow_goal') +
          '</span><span class="flow-node-v">' + fmtPoseShort(m.goal) + '</span></div>';
      } else {
        body +=
          '<div class="flow-node-line"><span class="flow-node-k">' + t('infer.flow_start') +
          '</span><span class="flow-node-v">' + fmtPoseShort(m.start) + '</span></div>';
        if (m.motion_mode !== 'infer') {
          body +=
            '<div class="flow-node-line"><span class="flow-node-k">' + t('infer.flow_goal') +
            '</span><span class="flow-node-v">' + fmtPoseShort(m.goal) + '</span></div>';
        } else {
          const tc = normalizeTermCond(m.term_cond);
          const dirLab = tc.direction === 'z_fall' ? t('infer.flow_z_fall') : t('infer.flow_z_rise');
          const th = tc.threshold != null ? String(tc.threshold) : '—';
          body +=
            '<div class="flow-node-line"><span class="flow-node-k">' + t('infer.flow_term_cond') +
            '</span><span class="flow-node-v">' + dirLab + ' ' + th + '</span></div>';
        }
      }
      if (err) body += '<div class="flow-node-state">' + err + '</div>';
      el.innerHTML =
        '<div class="flow-node-head">' +
          '<span>' + typeLabel(m) + '</span>' +
          '<span class="flow-node-state">' + stateLabel(st) + '</span>' +
        '</div>' +
        '<div class="flow-node-body">' + body + '</div>' +
        '<span class="flow-port in" data-port="in" title="in"></span>' +
        '<span class="flow-port out" data-port="out" title="out"></span>';
      layer.appendChild(el);
      wireNode(el, m);
    });
    redrawEdges();
  }

  function selectModule(id) {
    editor.selectedId = id;
    editor.selectedEdgeId = null;
    renderNodes();
    renderInspector();
  }

  /** Update selection without destroying node DOM (safe during drag). */
  function markSelected(id) {
    editor.selectedId = id;
    editor.selectedEdgeId = null;
    if (editor.els.nodes) {
      editor.els.nodes.querySelectorAll('.flow-node').forEach((n) => {
        n.classList.toggle('selected', n.dataset.id === id);
      });
    }
    redrawEdges();
    renderInspector();
  }

  function beginNodeDrag(mod, el, clientX, clientY) {
    markSelected(mod.id);
    el.classList.add('flow-dragging');
    const start = worldPointFromClient(clientX, clientY);
    const ox = mod.x;
    const oy = mod.y;
    let moved = false;
    const onMove = (e2) => {
      if (!el.isConnected) return;
      e2.preventDefault();
      const p = worldPointFromClient(e2.clientX, e2.clientY);
      const nx = Math.round(ox + (p.x - start.x));
      const ny = Math.round(oy + (p.y - start.y));
      if (nx !== mod.x || ny !== mod.y) moved = true;
      mod.x = nx;
      mod.y = ny;
      el.style.left = mod.x + 'px';
      el.style.top = mod.y + 'px';
      redrawEdges();
    };
    const onUp = () => {
      window.removeEventListener('pointermove', onMove, true);
      window.removeEventListener('pointerup', onUp, true);
      window.removeEventListener('pointercancel', onUp, true);
      if (el.isConnected) el.classList.remove('flow-dragging');
      if (moved) persist();
    };
    window.addEventListener('pointermove', onMove, true);
    window.addEventListener('pointerup', onUp, true);
    window.addEventListener('pointercancel', onUp, true);
  }

  function deleteSelected() {
    if (editor.selectedEdgeId) {
      deleteEdgeById(editor.selectedEdgeId);
      return;
    }
    if (!editor.selectedId) return;
    const id = editor.selectedId;
    editor.graph.modules = editor.graph.modules.filter((m) => m.id !== id);
    editor.graph.edges = editor.graph.edges.filter((e) => e.from !== id && e.to !== id);
    delete editor.moduleStates[id];
    editor.selectedId = null;
    editor.selectedEdgeId = null;
    persist();
    renderNodes();
    renderInspector();
  }

  /** Resolve link drop target: hit node, or nearest node (screen-space rect distance). */
  function distPointToRect(x, y, r) {
    const dx = Math.max(r.left - x, 0, x - r.right);
    const dy = Math.max(r.top - y, 0, y - r.bottom);
    return Math.hypot(dx, dy);
  }

  function resolveLinkTarget(clientX, clientY, fromId) {
    if (!Number.isFinite(clientX) || !Number.isFinite(clientY)) return null;
    const nodes = editor.els.nodes ? editor.els.nodes.querySelectorAll('.flow-node') : [];

    // Prefer exact hit (ignore SVG / captured source port)
    const stack = (typeof document.elementsFromPoint === 'function')
      ? document.elementsFromPoint(clientX, clientY)
      : [document.elementFromPoint(clientX, clientY)].filter(Boolean);
    for (let i = 0; i < stack.length; i++) {
      const el = stack[i];
      if (!el || !el.closest) continue;
      const node = el.closest('.flow-node');
      if (node && node.dataset.id && node.dataset.id !== fromId) {
        return node.dataset.id;
      }
    }

    // Snap to nearest other node by distance to its bounding box
    let bestId = null;
    let bestDist = LINK_SNAP_PX;
    nodes.forEach((node) => {
      const id = node.dataset.id;
      if (!id || id === fromId) return;
      const d = distPointToRect(clientX, clientY, node.getBoundingClientRect());
      if (d < bestDist) {
        bestDist = d;
        bestId = id;
      }
    });
    return bestId;
  }

  function clearLinkHighlights() {
    if (!editor.els.nodes) return;
    editor.els.nodes.querySelectorAll('.flow-node').forEach((n) => {
      n.classList.remove('flow-link-target', 'flow-linking-from');
    });
  }

  function commitLink(fromId, toId) {
    if (!fromId || !toId || fromId === toId) return false;
    editor.graph.edges = editor.graph.edges.filter((e) => e.from !== fromId);
    editor.graph.edges.push({ id: uid('e'), from: fromId, to: toId });
    persist();
    const v = validateChain(editor.graph.modules, editor.graph.edges);
    if (!v.ok) setHint(t('infer.flow_warn_' + v.error) || v.error);
    else setHint(t('infer.flow_hint_linked'));
    return true;
  }

  function endLinking(clientX, clientY) {
    const fromId = editor.linking && editor.linking.fromId;
    const toId = fromId ? resolveLinkTarget(clientX, clientY, fromId) : null;
    clearLinkHighlights();
    editor.linking = null;
    if (fromId && toId) commitLink(fromId, toId);
    renderNodes();
    // Layout then redraw so port heights are accurate and the edge is visible
    requestAnimationFrame(function () { redrawEdges(); });
  }

  function beginLinkDrag(mod, clientX, clientY) {
    editor.linking = {
      fromId: mod.id,
      cur: worldPointFromClient(clientX, clientY),
    };
    const srcEl = editor.els.nodes && editor.els.nodes.querySelector('.flow-node[data-id="' + mod.id + '"]');
    if (srcEl) srcEl.classList.add('flow-linking-from');
    setHint(t('infer.flow_hint_linking'));
    redrawEdges();

    const onMove = (e2) => {
      if (!editor.linking) return;
      editor.linking.cur = worldPointFromClient(e2.clientX, e2.clientY);
      const hoverId = resolveLinkTarget(e2.clientX, e2.clientY, mod.id);
      if (editor.els.nodes) {
        editor.els.nodes.querySelectorAll('.flow-node').forEach((n) => {
          n.classList.toggle('flow-link-target', n.dataset.id === hoverId);
        });
      }
      redrawEdges();
    };
    const onUp = (e2) => {
      window.removeEventListener('pointermove', onMove, true);
      window.removeEventListener('pointerup', onUp, true);
      window.removeEventListener('pointercancel', onUp, true);
      endLinking(e2.clientX, e2.clientY);
    };
    window.addEventListener('pointermove', onMove, true);
    window.addEventListener('pointerup', onUp, true);
    window.addEventListener('pointercancel', onUp, true);
  }

  function wireNode(el, mod) {
    const head = el.querySelector('.flow-node-head');
    head.addEventListener('pointerdown', (ev) => {
      if (ev.button !== 0) return;
      if (editor.linking) return;
      ev.stopPropagation();
      ev.preventDefault();
      beginNodeDrag(mod, el, ev.clientX, ev.clientY);
    });
    el.addEventListener('click', (ev) => {
      ev.stopPropagation();
      // Click-to-connect: if an unfinished click-link is pending, complete it
      if (editor.linking && editor.linking.clickMode && editor.linking.fromId && editor.linking.fromId !== mod.id) {
        const fromId = editor.linking.fromId;
        clearLinkHighlights();
        editor.linking = null;
        commitLink(fromId, mod.id);
        renderNodes();
        return;
      }
      selectModule(mod.id);
    });
    // Body: left/center drag module; right edge starts a link
    el.addEventListener('pointerdown', (ev) => {
      if (ev.button !== 0 || editor.linking) return;
      if (ev.target.closest && (ev.target.closest('.flow-port') || ev.target.closest('.flow-node-head'))) return;
      const rect = el.getBoundingClientRect();
      const relX = (ev.clientX - rect.left) / Math.max(rect.width, 1);
      ev.stopPropagation();
      ev.preventDefault();
      if (relX >= 0.72) {
        beginLinkDrag(mod, ev.clientX, ev.clientY);
      } else {
        beginNodeDrag(mod, el, ev.clientX, ev.clientY);
      }
    });
    const out = el.querySelector('.flow-port.out');
    out.addEventListener('pointerdown', (ev) => {
      if (ev.button !== 0) return;
      ev.stopPropagation();
      ev.preventDefault();
      beginLinkDrag(mod, ev.clientX, ev.clientY);
    });
  }

  function readPoseFromLive() {
    const xyz = window.__armReadCartesian;
    const grip = window.__gripReadNorm;
    if (!Array.isArray(xyz) || xyz.length < 6) return null;
    const g = (grip != null && Number.isFinite(Number(grip))) ? Number(grip) : 0;
    return { xyzrpy: xyz.slice(0, 6).map(Number), gripper: g };
  }

  function timingFieldsHtml(tm) {
    const rows = [
      { id: 'flowInsTMin', key: 'infer.flow_t_min', step: '0.1', val: tm.t_min_s },
      { id: 'flowInsTMax', key: 'infer.flow_t_max', step: '0.1', val: tm.t_max_s },
      { id: 'flowInsVNorm', key: 'infer.flow_v_norm', step: '0.001', val: tm.v_norm_rad_s },
      { id: 'flowInsTj', key: 'infer.flow_tj', step: '0.01', val: tm.jerk_seg_frac },
      { id: 'flowInsTa', key: 'infer.flow_ta', step: '0.01', val: tm.accel_seg_frac },
    ];
    let html = '<div class="flow-field"><label>' + t('infer.flow_timing') + '</label>';
    html += '<div class="flow-timing-list">';
    rows.forEach((r) => {
      html +=
        '<div class="flow-timing-row">' +
          '<label for="' + r.id + '">' + t(r.key) + '</label>' +
          '<input type="number" step="' + r.step + '" id="' + r.id + '" value="' + r.val + '" />' +
        '</div>';
    });
    html += '</div></div>';
    return html;
  }

  function readTimingFromBox(box) {
    return {
      t_min_s: Number(box.querySelector('#flowInsTMin').value) || 0.1,
      t_max_s: Number(box.querySelector('#flowInsTMax').value) || 30,
      v_norm_rad_s: Number(box.querySelector('#flowInsVNorm').value) || 0.02,
      jerk_seg_frac: Number(box.querySelector('#flowInsTj').value) || 0.1,
      accel_seg_frac: Number(box.querySelector('#flowInsTa').value) || 0.15,
    };
  }

  function setElHidden(el, hide) {
    if (!el) return;
    el.hidden = !!hide;
    // Belt-and-suspenders: .flow-field { display:flex } can override [hidden]
    el.style.display = hide ? 'none' : '';
  }

  function syncInspectorModeVisibility(box, mode) {
    const isInfer = mode === 'infer';
    setElHidden(box.querySelector('.flow-term-block'), !isInfer);
    setElHidden(box.querySelector('.flow-goal-block'), isInfer);
  }

  function applyPoseCheckModule(box, m) {
    const goal = readPoseFields(box, 'flowInsGoal_');
    if (!goal) {
      // Fallback: allow CSV paste in a hidden/legacy textarea if present
      const ta = box.querySelector('#flowInsGoal');
      const parsed = ta ? parsePoseCsv(ta.value) : null;
      if (!parsed) {
        setHint(t('infer.flow_bad_pose'));
        return false;
      }
      m.goal = parsed;
    } else {
      m.goal = goal;
    }
    const titleEl = box.querySelector('#flowInsTitle');
    m.title = titleEl ? String(titleEl.value || '') : (m.title || '');
    m.type = 'pose_check';
    m.motion_mode = 'program';
    m.timing = readTimingFromBox(box);
    persist();
    renderNodes();
    renderInspector();
    setHint(t('infer.flow_saved'));
    return true;
  }

  function renderInspector() {
    const box = editor.els.inspector;
    if (!box) return;

    if (editor.selectedEdgeId) {
      const edge = editor.graph.edges.find((e) => e.id === editor.selectedEdgeId);
      if (!edge) {
        editor.selectedEdgeId = null;
      } else {
        const fromM = moduleById(edge.from);
        const toM = moduleById(edge.to);
        box.innerHTML =
          '<h3 class="flow-inspector-title">' + t('infer.flow_inspector') + ' · ' + t('infer.flow_edge') + '</h3>' +
          '<p class="flow-inspector-empty">' +
            escapeAttr(typeLabel(fromM || { type: 'basic' })) + ' → ' +
            escapeAttr(typeLabel(toM || { type: 'basic' })) +
          '</p>' +
          '<div class="flow-inspector-actions">' +
            '<button type="button" class="primary" id="flowInsDeleteEdge">' + t('infer.flow_delete_edge') + '</button>' +
          '</div>';
        const del = box.querySelector('#flowInsDeleteEdge');
        if (del) {
          del.onclick = (ev) => {
            ev.preventDefault();
            deleteEdgeById(edge.id);
            renderInspector();
          };
        }
        return;
      }
    }

    const m = moduleById(editor.selectedId);
    if (!m) {
      box.innerHTML =
        '<h3 class="flow-inspector-title" data-i18n="infer.flow_inspector">' + t('infer.flow_inspector') + '</h3>' +
        '<p class="flow-inspector-empty" data-i18n="infer.flow_inspector_empty">' + t('infer.flow_inspector_empty') + '</p>';
      return;
    }

    if (m.type === 'pose_check') {
      const tm = m.timing || defaultTiming();
      box.innerHTML =
        '<h3 class="flow-inspector-title">' + t('infer.flow_inspector') + ' · ' + t('infer.flow_pose_check') + '</h3>' +
        '<div class="flow-field"><label>' + t('infer.flow_title') + '</label>' +
          '<input type="text" id="flowInsTitle" value="' + escapeAttr(m.title || '') + '" /></div>' +
        '<div class="flow-field"><label>' + t('infer.flow_mode') + '</label>' +
          '<input type="text" value="' + escapeAttr(t('infer.flow_mode_fixed_program')) + '" disabled /></div>' +
        poseFieldsHtml('flowInsGoal_', m.goal || defaultPose7(), t('infer.flow_goal_only'), 'flowInsFillGoal') +
        timingFieldsHtml(tm) +
        '<div class="flow-inspector-actions">' +
          '<button type="button" class="primary" id="flowInsApply">' + t('btn.apply') + '</button>' +
          '<button type="button" id="flowInsDelete">' + t('infer.flow_delete') + '</button>' +
        '</div>';
      bindSyncPose(box, 'flowInsFillGoal', (p) => fillPoseFields(box, 'flowInsGoal_', p));
      const applyBtn = box.querySelector('#flowInsApply');
      const delBtn = box.querySelector('#flowInsDelete');
      if (delBtn) delBtn.onclick = () => { deleteSelected(); };
      if (applyBtn) {
        applyBtn.onclick = (ev) => {
          ev.preventDefault();
          ev.stopPropagation();
          const mod = moduleById(editor.selectedId);
          if (!mod) return;
          applyPoseCheckModule(box, mod);
        };
      }
      return;
    }

    const tc = normalizeTermCond(m.term_cond);
    const tol = m.start_tol || defaultTol();
    const tm = m.timing || defaultTiming();
    const isInfer = m.motion_mode === 'infer';
    box.innerHTML =
      '<h3 class="flow-inspector-title">' + t('infer.flow_inspector') + '</h3>' +
      '<div class="flow-field"><label>' + t('infer.flow_title') + '</label>' +
        '<input type="text" id="flowInsTitle" value="' + escapeAttr(m.title || '') + '" /></div>' +
      '<div class="flow-field"><label>' + t('infer.flow_mode') + '</label>' +
        '<select id="flowInsMode">' +
          '<option value="program"' + (!isInfer ? ' selected' : '') + '>' + t('infer.flow_mode_program') + '</option>' +
          '<option value="infer"' + (isInfer ? ' selected' : '') + '>' + t('infer.flow_mode_infer') + '</option>' +
        '</select></div>' +
      poseCsvFieldHtml('flowInsStart', m.start, t('infer.flow_start'), 'flowInsFillStart') +
      '<div class="flow-goal-block"' + (isInfer ? ' hidden' : '') + '>' +
        poseCsvFieldHtml('flowInsGoal', m.goal, t('infer.flow_goal'), 'flowInsFillGoal') +
      '</div>' +
      '<div class="flow-field flow-term-block"' + (isInfer ? '' : ' hidden') + '>' +
        '<label>' + t('infer.flow_term_cond') + '</label>' +
        '<div class="flow-term-row">' +
          '<div><label>' + t('infer.flow_term_dir') + '</label>' +
            '<select id="flowInsTermDir">' +
              '<option value="z_rise"' + (tc.direction !== 'z_fall' ? ' selected' : '') + '>' + t('infer.flow_z_rise') + '</option>' +
              '<option value="z_fall"' + (tc.direction === 'z_fall' ? ' selected' : '') + '>' + t('infer.flow_z_fall') + '</option>' +
            '</select></div>' +
          '<div><label>' + t('infer.flow_term_threshold') + '</label>' +
            '<input type="number" step="0.001" id="flowInsTermTh" value="' +
              (tc.threshold != null ? tc.threshold : '') + '" /></div>' +
        '</div></div>' +
      '<div class="flow-field"><label>' + t('infer.flow_tol') + '</label>' +
        '<div class="flow-timing-grid">' +
          '<input type="number" step="0.001" id="flowInsTolPos" title="pos_m" value="' + tol.pos_m + '" />' +
          '<input type="number" step="0.001" id="flowInsTolRot" title="rot_rad" value="' + tol.rot_rad + '" />' +
          '<input type="number" step="0.001" id="flowInsTolGrip" title="grip" value="' + tol.grip + '" />' +
        '</div></div>' +
      timingFieldsHtml(tm) +
      '<div class="flow-inspector-actions">' +
        '<button type="button" class="primary" id="flowInsApply">' + t('btn.apply') + '</button>' +
        '<button type="button" id="flowInsDelete">' + t('infer.flow_delete') + '</button>' +
      '</div>';

    const modeEl = box.querySelector('#flowInsMode');
    syncInspectorModeVisibility(box, modeEl.value);
    modeEl.addEventListener('change', () => {
      const mode = modeEl.value === 'infer' ? 'infer' : 'program';
      // Persist mode immediately so node card + inspector stay in sync
      m.motion_mode = mode;
      if (mode === 'infer') {
        m.term_cond = normalizeTermCond(m.term_cond);
      }
      persist();
      renderNodes();
      renderInspector();
    });
    bindSyncPose(box, 'flowInsFillStart', (p) => {
      const el = box.querySelector('#flowInsStart');
      if (el) el.value = poseToCsv(p);
    });
    bindSyncPose(box, 'flowInsFillGoal', (p) => {
      const el = box.querySelector('#flowInsGoal');
      if (el) el.value = poseToCsv(p);
    });
    box.querySelector('#flowInsDelete').addEventListener('click', deleteSelected);
    box.querySelector('#flowInsApply').addEventListener('click', () => {
      const start = parsePoseCsv(box.querySelector('#flowInsStart').value);
      if (!start) {
        setHint(t('infer.flow_bad_pose'));
        return;
      }
      const mode = modeEl.value === 'infer' ? 'infer' : 'program';
      let goal = m.goal || defaultPose7();
      if (mode !== 'infer') {
        goal = parsePoseCsv(box.querySelector('#flowInsGoal').value);
        if (!goal) {
          setHint(t('infer.flow_bad_pose'));
          return;
        }
      }
      const dir = box.querySelector('#flowInsTermDir').value === 'z_fall' ? 'z_fall' : 'z_rise';
      const thRaw = box.querySelector('#flowInsTermTh').value;
      const term = {
        direction: dir,
        threshold: thRaw === '' ? null : Number(thRaw),
      };
      if (mode === 'infer' && (term.threshold == null || !Number.isFinite(term.threshold))) {
        setHint(t('infer.flow_warn_missing_term'));
        return;
      }
      m.title = String(box.querySelector('#flowInsTitle').value || '');
      m.motion_mode = mode;
      m.start = start;
      m.goal = goal;
      m.term_cond = term;
      m.start_tol = {
        pos_m: Number(box.querySelector('#flowInsTolPos').value) || 0.01,
        rot_rad: Number(box.querySelector('#flowInsTolRot').value) || 0.05,
        grip: Number(box.querySelector('#flowInsTolGrip').value) || 0.05,
      };
      m.timing = readTimingFromBox(box);
      persist();
      renderNodes();
      renderInspector();
      setHint(t('infer.flow_saved'));
    });
  }

  function addModuleAt(type, clientX, clientY) {
    const p = worldPointFromClient(clientX, clientY);
    const m = createModule(type, p.x - NODE_W / 2, p.y - 40);
    editor.graph.modules.push(m);
    persist();
    selectModule(m.id);
  }

  function fitView() {
    editor.pan = { x: 40, y: 40 };
    editor.zoom = 1;
    applyWorldTransform();
  }

  function clearGraph() {
    editor.graph = emptyGraph();
    editor.selectedId = null;
    editor.selectedEdgeId = null;
    editor.moduleStates = {};
    persist();
    renderNodes();
    renderInspector();
    setHint(t('infer.flow_cleared'));
  }

  function setModuleState(id, state, error) {
    editor.moduleStates[id] = { state: state || 'idle', error: error || '' };
    renderNodes();
  }

  function resetModuleStates() {
    editor.moduleStates = {};
    editor.graph.modules.forEach((m) => {
      editor.moduleStates[m.id] = { state: 'idle', error: '' };
    });
    renderNodes();
  }

  function getGraph() {
    return editor.graph;
  }

  function graphForExport() {
    const g = editor.graph || emptyGraph();
    return {
      version: 1,
      modules: (g.modules || []).map((m) => migrateModule(m)).filter(Boolean),
      edges: (g.edges || []).map((e) => ({
        id: e.id || uid('e'),
        from: String(e.from || ''),
        to: String(e.to || ''),
      })).filter((e) => e.from && e.to),
      meta: {
        name: (g.meta && g.meta.name) || '',
        updated_at: new Date().toISOString(),
        exported_at: new Date().toISOString(),
      },
    };
  }

  function parseGraphPayload(raw) {
    let data = raw;
    if (typeof raw === 'string') {
      data = JSON.parse(raw);
    }
    if (!data || typeof data !== 'object') throw new Error('not_object');
    // Allow wrapping as { graph: {...} }
    if (data.graph && typeof data.graph === 'object') data = data.graph;
    if (!Array.isArray(data.modules) || !Array.isArray(data.edges)) {
      throw new Error('bad_shape');
    }
    const modules = data.modules.map(migrateModule).filter(Boolean);
    const moduleIds = new Set(modules.map((m) => m.id));
    const edges = data.edges
      .map((e) => ({
        id: (e && e.id) || uid('e'),
        from: String((e && e.from) || ''),
        to: String((e && e.to) || ''),
      }))
      .filter((e) => e.from && e.to && moduleIds.has(e.from) && moduleIds.has(e.to));
    return {
      version: 1,
      modules: modules,
      edges: edges,
      meta: {
        name: (data.meta && data.meta.name) || '',
        updated_at: new Date().toISOString(),
      },
    };
  }

  function setGraph(g, opts) {
    opts = opts || {};
    editor.graph = g || emptyGraph();
    editor.selectedId = null;
    editor.selectedEdgeId = null;
    editor.moduleStates = {};
    if (opts.persist !== false) persist();
    renderNodes();
    renderInspector();
    fitView();
  }

  function exportGraphJson() {
    const payload = graphForExport();
    const text = JSON.stringify(payload, null, 2);
    const stamp = new Date().toISOString().replace(/[:.]/g, '-').slice(0, 19);
    const name = (payload.meta && payload.meta.name) ? String(payload.meta.name).replace(/[^\w.-]+/g, '_') : 'flow';
    const filename = 'sensors-dcs-flow-' + (name || 'flow') + '-' + stamp + '.json';
    const blob = new Blob([text], { type: 'application/json;charset=utf-8' });
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = filename;
    document.body.appendChild(a);
    a.click();
    a.remove();
    setTimeout(function () { URL.revokeObjectURL(url); }, 1500);
    setHint(t('infer.flow_exported'));
  }

  function importGraphJsonText(text) {
    let g;
    try {
      g = parseGraphPayload(text);
    } catch (err) {
      const code = String((err && err.message) || err || '');
      if (code === 'bad_shape' || code === 'not_object') {
        setHint(t('infer.flow_import_bad'));
      } else {
        setHint(t('infer.flow_import_fail'));
      }
      return false;
    }
    setGraph(g);
    setHint(t('infer.flow_imported', { n: g.modules.length }));
    return true;
  }

  function bindToolDrag(el, type) {
    if (!el) return;
    el.addEventListener('dragstart', (ev) => {
      ev.dataTransfer.setData('text/flow-type', type);
      ev.dataTransfer.effectAllowed = 'copy';
    });
  }

  function init() {
    const page = document.getElementById('infPageFlow');
    if (!page || page.dataset.flowInited === '1') return;
    page.dataset.flowInited = '1';

    editor.els.wrap = document.getElementById('infFlowCanvasWrap');
    editor.els.world = document.getElementById('infFlowWorld');
    editor.els.edges = document.getElementById('infFlowEdges');
    editor.els.nodes = document.getElementById('infFlowNodes');
    editor.els.inspector = document.getElementById('infFlowInspector');
    editor.els.hint = document.getElementById('infFlowHint');
    editor.els.toolBasic = document.getElementById('infFlowToolBasic');
    editor.els.toolPoseCheck = document.getElementById('infFlowToolPoseCheck');

    editor.graph = loadGraph();
    applyWorldTransform();
    renderNodes();
    renderInspector();

    bindToolDrag(editor.els.toolBasic, 'basic');
    bindToolDrag(editor.els.toolPoseCheck, 'pose_check');
    if (editor.els.wrap) {
      editor.els.wrap.addEventListener('dragover', (ev) => {
        ev.preventDefault();
        ev.dataTransfer.dropEffect = 'copy';
      });
      editor.els.wrap.addEventListener('drop', (ev) => {
        ev.preventDefault();
        const typ = ev.dataTransfer.getData('text/flow-type') || 'basic';
        if (typ === 'basic' || typ === 'pose_check') addModuleAt(typ, ev.clientX, ev.clientY);
      });
      editor.els.wrap.addEventListener('click', (ev) => {
        if (ev.target && ev.target.closest && ev.target.closest('.flow-edge-group')) return;
        editor.selectedId = null;
        editor.selectedEdgeId = null;
        renderNodes();
        renderInspector();
      });
      editor.els.wrap.addEventListener('wheel', (ev) => {
        ev.preventDefault();
        const factor = ev.deltaY > 0 ? 0.92 : 1.08;
        editor.zoom = Math.max(0.4, Math.min(2.2, editor.zoom * factor));
        applyWorldTransform();
      }, { passive: false });

      let panning = null;
      editor.els.wrap.addEventListener('pointerdown', (ev) => {
        if (ev.button === 1 || (ev.button === 0 && ev.spaceKey) || (ev.button === 0 && ev.altKey)) {
          panning = { x: ev.clientX - editor.pan.x, y: ev.clientY - editor.pan.y };
          ev.preventDefault();
        }
      });
      window.addEventListener('pointermove', (ev) => {
        if (!panning) return;
        editor.pan.x = ev.clientX - panning.x;
        editor.pan.y = ev.clientY - panning.y;
        applyWorldTransform();
      });
      window.addEventListener('pointerup', () => { panning = null; });

      let spaceDown = false;
      window.addEventListener('keydown', (ev) => {
        if (ev.code === 'Space') spaceDown = true;
        if ((ev.key === 'Delete' || ev.key === 'Backspace') && (editor.selectedId || editor.selectedEdgeId)) {
          const tag = (ev.target && ev.target.tagName) || '';
          if (tag === 'INPUT' || tag === 'TEXTAREA' || tag === 'SELECT') return;
          deleteSelected();
        }
      });
      window.addEventListener('keyup', (ev) => {
        if (ev.code === 'Space') spaceDown = false;
      });
      editor.els.wrap.addEventListener('pointerdown', (ev) => {
        if (ev.button !== 0 || !spaceDown) return;
        if (ev.target.closest && ev.target.closest('.flow-node')) return;
        panning = { x: ev.clientX - editor.pan.x, y: ev.clientY - editor.pan.y };
        ev.preventDefault();
      });
    }

    const btnFit = document.getElementById('infFlowFit');
    const btnClear = document.getElementById('infFlowClear');
    const btnExport = document.getElementById('infFlowExport');
    const btnImport = document.getElementById('infFlowImport');
    const fileImport = document.getElementById('infFlowImportFile');
    if (btnFit) btnFit.addEventListener('click', fitView);
    if (btnClear) btnClear.addEventListener('click', clearGraph);
    if (btnExport) btnExport.addEventListener('click', exportGraphJson);
    if (btnImport && fileImport) {
      btnImport.addEventListener('click', () => { fileImport.value = ''; fileImport.click(); });
      fileImport.addEventListener('change', () => {
        const f = fileImport.files && fileImport.files[0];
        if (!f) return;
        const reader = new FileReader();
        reader.onload = () => {
          importGraphJsonText(String(reader.result || ''));
        };
        reader.onerror = () => { setHint(t('infer.flow_import_fail')); };
        reader.readAsText(f);
      });
    }

    const v = validateChain(editor.graph.modules, editor.graph.edges);
    if (editor.graph.modules.length && !v.ok) {
      setHint(t('infer.flow_warn_' + v.error) || v.error);
    } else {
      setHint(t('infer.flow_hint_ready'));
    }
  }

  window.__flowEditor = {
    init: init,
    getGraph: getGraph,
    setGraph: setGraph,
    exportGraphJson: exportGraphJson,
    importGraphJsonText: importGraphJsonText,
    validateChain: validateChain,
    setModuleState: setModuleState,
    resetModuleStates: resetModuleStates,
    setHint: setHint,
    persist: persist,
    selectModule: selectModule,
    render: function () { renderNodes(); renderInspector(); },
  };

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', init);
  } else {
    init();
  }
})();
