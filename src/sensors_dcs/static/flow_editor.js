/**
 * Infer Tab · Flow editor (P0): toolbox, nodes, edges, inspector, localStorage.
 * Runtime hooks: window.__flowEditor / window.FlowRuntime
 */
(function () {
  'use strict';

  const LS_KEY = 'dcs.inf.flow.graph';
  const NODE_W = 216;

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
      term_cond: { z_rise_to: null, z_fall_to: null },
      start_tol: defaultTol(),
      timing: defaultTiming(),
    };
  }

  function emptyGraph() {
    return { version: 1, modules: [], edges: [], meta: { name: '', updated_at: '' } };
  }

  function loadGraph() {
    try {
      const raw = localStorage.getItem(LS_KEY);
      if (!raw) return emptyGraph();
      const g = JSON.parse(raw);
      if (!g || !Array.isArray(g.modules) || !Array.isArray(g.edges)) return emptyGraph();
      g.version = 1;
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
    return a.slice(0, 3).map((v) => Number(v).toFixed(2)).join(',') + ' | g ' + Number(g).toFixed(2);
  }

  function parsePoseCsv(text) {
    const parts = String(text || '').trim().split(/[,\s;]+/).filter(Boolean).map(Number);
    if (parts.length < 6 || parts.some((v) => !Number.isFinite(v))) return null;
    const grip = parts.length >= 7 && Number.isFinite(parts[6]) ? parts[6] : 0;
    return { xyzrpy: parts.slice(0, 6), gripper: grip };
  }

  function poseToCsv(p) {
    const a = (p && p.xyzrpy) || [0, 0, 0, 0, 0, 0];
    const g = (p && p.gripper != null) ? p.gripper : 0;
    return a.map((v) => Number(v).toFixed(4)).join(',') + ',' + Number(g).toFixed(3);
  }

  const editor = {
    graph: emptyGraph(),
    selectedId: null,
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
    const h = 88;
    return {
      x: mod.x + (which === 'out' ? NODE_W : 0),
      y: mod.y + h / 2,
    };
  }

  function redrawEdges() {
    const svg = editor.els.edges;
    if (!svg) return;
    const parts = [
      '<defs><marker id="flowArrow" markerWidth="8" markerHeight="8" refX="6" refY="3" orient="auto">' +
        '<path d="M0,0 L6,3 L0,6 Z" fill="currentColor" /></marker></defs>',
    ];
    editor.graph.edges.forEach((e) => {
      const a = moduleById(e.from);
      const b = moduleById(e.to);
      if (!a || !b) return;
      const p0 = portCenter(a, 'out');
      const p1 = portCenter(b, 'in');
      const mx = (p0.x + p1.x) / 2;
      parts.push(
        '<path d="M' + p0.x + ',' + p0.y + ' C' + mx + ',' + p0.y + ' ' + mx + ',' + p1.y + ' ' + p1.x + ',' + p1.y + '" />'
      );
    });
    if (editor.linking && editor.linking.fromId && editor.linking.cur) {
      const a = moduleById(editor.linking.fromId);
      if (a) {
        const p0 = portCenter(a, 'out');
        const p1 = editor.linking.cur;
        const mx = (p0.x + p1.x) / 2;
        parts.push(
          '<path class="flow-edge-temp" d="M' + p0.x + ',' + p0.y +
            ' C' + mx + ',' + p0.y + ' ' + mx + ',' + p1.y + ' ' + p1.x + ',' + p1.y + '" />'
        );
      }
    }
    svg.innerHTML = parts.join('');
    svg.setAttribute('width', '4000');
    svg.setAttribute('height', '3000');
  }

  function stateLabel(st) {
    return st || 'idle';
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
      el.dataset.state = st;
      el.style.left = m.x + 'px';
      el.style.top = m.y + 'px';
      const modeLab = m.motion_mode === 'infer' ? t('infer.flow_mode_infer') : t('infer.flow_mode_program');
      el.innerHTML =
        '<div class="flow-node-head">' +
          '<span>' + (m.title || t('infer.flow_basic')) + '</span>' +
          '<span class="flow-node-state">' + stateLabel(st) + '</span>' +
        '</div>' +
        '<div class="flow-node-body">' +
          '<div><strong>' + modeLab + '</strong></div>' +
          '<div>' + t('infer.flow_start') + ': ' + fmtPoseShort(m.start) + '</div>' +
          '<div>' + t('infer.flow_goal') + ': ' + fmtPoseShort(m.goal) + '</div>' +
          (err ? '<div class="flow-node-state">' + err + '</div>' : '') +
        '</div>' +
        '<span class="flow-port in" data-port="in"></span>' +
        '<span class="flow-port out" data-port="out"></span>';
      layer.appendChild(el);
      wireNode(el, m);
    });
    redrawEdges();
  }

  function selectModule(id) {
    editor.selectedId = id;
    renderNodes();
    renderInspector();
  }

  function deleteSelected() {
    if (!editor.selectedId) return;
    const id = editor.selectedId;
    editor.graph.modules = editor.graph.modules.filter((m) => m.id !== id);
    editor.graph.edges = editor.graph.edges.filter((e) => e.from !== id && e.to !== id);
    delete editor.moduleStates[id];
    editor.selectedId = null;
    persist();
    renderNodes();
    renderInspector();
  }

  function wireNode(el, mod) {
    const head = el.querySelector('.flow-node-head');
    head.addEventListener('pointerdown', (ev) => {
      if (ev.button !== 0) return;
      ev.stopPropagation();
      selectModule(mod.id);
      const start = worldPointFromClient(ev.clientX, ev.clientY);
      const ox = mod.x;
      const oy = mod.y;
      const onMove = (e2) => {
        const p = worldPointFromClient(e2.clientX, e2.clientY);
        mod.x = Math.round(ox + (p.x - start.x));
        mod.y = Math.round(oy + (p.y - start.y));
        el.style.left = mod.x + 'px';
        el.style.top = mod.y + 'px';
        redrawEdges();
      };
      const onUp = () => {
        window.removeEventListener('pointermove', onMove);
        window.removeEventListener('pointerup', onUp);
        persist();
      };
      window.addEventListener('pointermove', onMove);
      window.addEventListener('pointerup', onUp);
    });
    el.addEventListener('click', (ev) => {
      ev.stopPropagation();
      selectModule(mod.id);
    });
    const out = el.querySelector('.flow-port.out');
    out.addEventListener('pointerdown', (ev) => {
      ev.stopPropagation();
      ev.preventDefault();
      editor.linking = { fromId: mod.id, cur: portCenter(mod, 'out') };
      const onMove = (e2) => {
        editor.linking.cur = worldPointFromClient(e2.clientX, e2.clientY);
        redrawEdges();
      };
      const onUp = (e2) => {
        window.removeEventListener('pointermove', onMove);
        window.removeEventListener('pointerup', onUp);
        const target = document.elementFromPoint(e2.clientX, e2.clientY);
        const port = target && target.closest ? target.closest('.flow-port.in') : null;
        const node = port && port.closest ? port.closest('.flow-node') : null;
        if (node && node.dataset.id && node.dataset.id !== mod.id) {
          const toId = node.dataset.id;
          editor.graph.edges = editor.graph.edges.filter((e) => e.from !== mod.id);
          editor.graph.edges.push({ id: uid('e'), from: mod.id, to: toId });
          persist();
          const v = validateChain(editor.graph.modules, editor.graph.edges);
          if (!v.ok) setHint(t('infer.flow_warn_' + v.error) || v.error);
          else setHint(t('infer.flow_hint_linked'));
        }
        editor.linking = null;
        renderNodes();
      };
      window.addEventListener('pointermove', onMove);
      window.addEventListener('pointerup', onUp);
    });
  }

  function readPoseFromLive() {
    const xyz = window.__armReadCartesian;
    const grip = window.__gripReadNorm;
    if (!Array.isArray(xyz) || xyz.length < 6) return null;
    const g = (grip != null && Number.isFinite(Number(grip))) ? Number(grip) : 0;
    return { xyzrpy: xyz.slice(0, 6).map(Number), gripper: g };
  }

  function renderInspector() {
    const box = editor.els.inspector;
    if (!box) return;
    const m = moduleById(editor.selectedId);
    if (!m) {
      box.innerHTML =
        '<h3 class="flow-inspector-title" data-i18n="infer.flow_inspector">' + t('infer.flow_inspector') + '</h3>' +
        '<p class="flow-inspector-empty" data-i18n="infer.flow_inspector_empty">' + t('infer.flow_inspector_empty') + '</p>';
      return;
    }
    const tc = m.term_cond || {};
    const tol = m.start_tol || defaultTol();
    const tm = m.timing || defaultTiming();
    box.innerHTML =
      '<h3 class="flow-inspector-title">' + t('infer.flow_inspector') + '</h3>' +
      '<div class="flow-field"><label>' + t('infer.flow_title') + '</label>' +
        '<input type="text" id="flowInsTitle" value="' + (m.title || '').replace(/"/g, '&quot;') + '" /></div>' +
      '<div class="flow-field"><label>' + t('infer.flow_mode') + '</label>' +
        '<select id="flowInsMode">' +
          '<option value="program"' + (m.motion_mode === 'program' ? ' selected' : '') + '>' + t('infer.flow_mode_program') + '</option>' +
          '<option value="infer"' + (m.motion_mode === 'infer' ? ' selected' : '') + '>' + t('infer.flow_mode_infer') + '</option>' +
        '</select></div>' +
      '<div class="flow-field"><label>' + t('infer.flow_start') + ' (x,y,z,rx,ry,rz,g)</label>' +
        '<textarea id="flowInsStart">' + poseToCsv(m.start) + '</textarea></div>' +
      '<div class="flow-field"><label>' + t('infer.flow_goal') + ' (x,y,z,rx,ry,rz,g)</label>' +
        '<textarea id="flowInsGoal">' + poseToCsv(m.goal) + '</textarea></div>' +
      '<div class="flow-field flow-term-block"' + (m.motion_mode === 'infer' ? '' : ' hidden') + '>' +
        '<label>' + t('infer.flow_term_cond') + '</label>' +
        '<div class="flow-term-row">' +
          '<div><label>' + t('infer.flow_z_rise') + '</label>' +
            '<input type="number" step="0.001" id="flowInsZRise" value="' + (tc.z_rise_to != null ? tc.z_rise_to : '') + '" /></div>' +
          '<div><label>' + t('infer.flow_z_fall') + '</label>' +
            '<input type="number" step="0.001" id="flowInsZFall" value="' + (tc.z_fall_to != null ? tc.z_fall_to : '') + '" /></div>' +
        '</div></div>' +
      '<div class="flow-field"><label>' + t('infer.flow_tol') + '</label>' +
        '<div class="flow-timing-grid">' +
          '<input type="number" step="0.001" id="flowInsTolPos" title="pos_m" value="' + tol.pos_m + '" />' +
          '<input type="number" step="0.001" id="flowInsTolRot" title="rot_rad" value="' + tol.rot_rad + '" />' +
          '<input type="number" step="0.001" id="flowInsTolGrip" title="grip" value="' + tol.grip + '" />' +
        '</div></div>' +
      '<div class="flow-field"><label>' + t('infer.flow_timing') + '</label>' +
        '<div class="flow-timing-grid">' +
          '<input type="number" step="0.1" id="flowInsTMin" title="t_min" value="' + tm.t_min_s + '" />' +
          '<input type="number" step="0.1" id="flowInsTMax" title="t_max" value="' + tm.t_max_s + '" />' +
          '<input type="number" step="0.001" id="flowInsVNorm" title="v_norm" value="' + tm.v_norm_rad_s + '" />' +
          '<input type="number" step="0.01" id="flowInsTj" title="Tj" value="' + tm.jerk_seg_frac + '" />' +
          '<input type="number" step="0.01" id="flowInsTa" title="Ta" value="' + tm.accel_seg_frac + '" />' +
        '</div></div>' +
      '<div class="flow-inspector-actions">' +
        '<button type="button" id="flowInsFillStart">' + t('infer.flow_fill_start') + '</button>' +
        '<button type="button" id="flowInsFillGoal">' + t('infer.flow_fill_goal') + '</button>' +
        '<button type="button" id="flowInsApply">' + t('btn.apply') + '</button>' +
        '<button type="button" id="flowInsDelete">' + t('infer.flow_delete') + '</button>' +
      '</div>';

    const modeEl = box.querySelector('#flowInsMode');
    modeEl.addEventListener('change', () => {
      const term = box.querySelector('.flow-term-block');
      if (term) term.hidden = modeEl.value !== 'infer';
    });
    box.querySelector('#flowInsFillStart').addEventListener('click', () => {
      const p = readPoseFromLive();
      if (!p) { setHint(t('infer.flow_need_read')); return; }
      box.querySelector('#flowInsStart').value = poseToCsv(p);
    });
    box.querySelector('#flowInsFillGoal').addEventListener('click', () => {
      const p = readPoseFromLive();
      if (!p) { setHint(t('infer.flow_need_read')); return; }
      box.querySelector('#flowInsGoal').value = poseToCsv(p);
    });
    box.querySelector('#flowInsDelete').addEventListener('click', deleteSelected);
    box.querySelector('#flowInsApply').addEventListener('click', () => {
      const start = parsePoseCsv(box.querySelector('#flowInsStart').value);
      const goal = parsePoseCsv(box.querySelector('#flowInsGoal').value);
      if (!start || !goal) {
        setHint(t('infer.flow_bad_pose'));
        return;
      }
      m.title = String(box.querySelector('#flowInsTitle').value || '');
      m.motion_mode = modeEl.value === 'infer' ? 'infer' : 'program';
      m.start = start;
      m.goal = goal;
      const zr = box.querySelector('#flowInsZRise').value;
      const zf = box.querySelector('#flowInsZFall').value;
      m.term_cond = {
        z_rise_to: zr === '' ? null : Number(zr),
        z_fall_to: zf === '' ? null : Number(zf),
      };
      m.start_tol = {
        pos_m: Number(box.querySelector('#flowInsTolPos').value) || 0.01,
        rot_rad: Number(box.querySelector('#flowInsTolRot').value) || 0.05,
        grip: Number(box.querySelector('#flowInsTolGrip').value) || 0.05,
      };
      m.timing = {
        t_min_s: Number(box.querySelector('#flowInsTMin').value) || 0.1,
        t_max_s: Number(box.querySelector('#flowInsTMax').value) || 30,
        v_norm_rad_s: Number(box.querySelector('#flowInsVNorm').value) || 0.02,
        jerk_seg_frac: Number(box.querySelector('#flowInsTj').value) || 0.1,
        accel_seg_frac: Number(box.querySelector('#flowInsTa').value) || 0.15,
      };
      persist();
      renderNodes();
      setHint(t('infer.flow_saved'));
    });
  }

  function addModuleAt(clientX, clientY) {
    const p = worldPointFromClient(clientX, clientY);
    const m = createBasicModule(p.x - NODE_W / 2, p.y - 40);
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

    editor.graph = loadGraph();
    applyWorldTransform();
    renderNodes();
    renderInspector();

    if (editor.els.toolBasic) {
      editor.els.toolBasic.addEventListener('dragstart', (ev) => {
        ev.dataTransfer.setData('text/flow-type', 'basic');
        ev.dataTransfer.effectAllowed = 'copy';
      });
    }
    if (editor.els.wrap) {
      editor.els.wrap.addEventListener('dragover', (ev) => {
        ev.preventDefault();
        ev.dataTransfer.dropEffect = 'copy';
      });
      editor.els.wrap.addEventListener('drop', (ev) => {
        ev.preventDefault();
        const typ = ev.dataTransfer.getData('text/flow-type');
        if (typ === 'basic') addModuleAt(ev.clientX, ev.clientY);
      });
      editor.els.wrap.addEventListener('click', () => {
        editor.selectedId = null;
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

      // Space+drag pan
      let spaceDown = false;
      window.addEventListener('keydown', (ev) => {
        if (ev.code === 'Space') spaceDown = true;
        if ((ev.key === 'Delete' || ev.key === 'Backspace') && editor.selectedId) {
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
    if (btnFit) btnFit.addEventListener('click', fitView);
    if (btnClear) btnClear.addEventListener('click', clearGraph);

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
