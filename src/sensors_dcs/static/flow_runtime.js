/**
 * Infer Tab · Flow runtime (P1/P2): program + infer + pose_check module execution.
 * Depends on window.__flow* adapters from viz.py and window.__flowEditor.
 */
(function () {
  'use strict';

  function t(key, vars) {
    try {
      if (typeof window.t === 'function') return window.t(key, vars);
    } catch (_) {}
    return key;
  }

  function sleepMs(ms) {
    return new Promise((resolve) => setTimeout(resolve, ms));
  }

  const state = {
    running: false,
    paused: false,
    gen: 0,
    currentModuleId: null,
    timerAccMs: 0,
    timerStartedAt: null,
    timerTick: null,
    lastElapsedMs: 0,
  };

  function ed() {
    return window.__flowEditor || null;
  }

  function setHint(msg) {
    if (ed() && ed().setHint) ed().setHint(msg);
  }

  function setMod(id, st, err) {
    if (ed() && ed().setModuleState) ed().setModuleState(id, st, err);
  }

  function formatElapsedMs(ms) {
    const s = Math.max(0, Number(ms) || 0) / 1000;
    if (s < 60) return s.toFixed(1) + 's';
    const m = Math.floor(s / 60);
    const rem = s - m * 60;
    return m + 'm' + rem.toFixed(1) + 's';
  }

  function flowElapsedMs() {
    let ms = state.timerAccMs;
    if (state.timerStartedAt != null) ms += Math.max(0, Date.now() - state.timerStartedAt);
    return ms;
  }

  function renderFlowElapsed(finalMs) {
    const el = document.getElementById('infFlowElapsed');
    if (!el) return;
    const ms = (finalMs != null) ? finalMs : flowElapsedMs();
    if (ms <= 0 && state.timerStartedAt == null && !state.timerAccMs && finalMs == null) {
      el.textContent = '—';
      return;
    }
    el.textContent = formatElapsedMs(ms);
  }

  function startFlowTimer() {
    state.timerAccMs = 0;
    state.timerStartedAt = Date.now();
    state.lastElapsedMs = 0;
    if (state.timerTick) clearInterval(state.timerTick);
    state.timerTick = setInterval(() => { renderFlowElapsed(); }, 200);
    renderFlowElapsed();
  }

  function pauseFlowTimer() {
    if (state.timerStartedAt != null) {
      state.timerAccMs += Math.max(0, Date.now() - state.timerStartedAt);
      state.timerStartedAt = null;
    }
    renderFlowElapsed();
  }

  function resumeFlowTimer() {
    if (state.timerStartedAt == null) state.timerStartedAt = Date.now();
    renderFlowElapsed();
  }

  function stopFlowTimer() {
    const total = flowElapsedMs();
    if (state.timerTick) { clearInterval(state.timerTick); state.timerTick = null; }
    state.timerStartedAt = null;
    state.timerAccMs = total;
    state.lastElapsedMs = total;
    renderFlowElapsed(total);
    return total;
  }

  function hasSucceededModules() {
    const editor = ed();
    if (!editor || !editor.getGraph) return false;
    const graph = editor.getGraph();
    const states = (editor.moduleStates) || {};
    return (graph.modules || []).some((m) => {
      const st = states[m.id] && states[m.id].state;
      return st === 'succeeded';
    });
  }

  function syncToolbar() {
    const runBtn = document.getElementById('infFlowRun');
    const pauseBtn = document.getElementById('infFlowPause');
    const resumeBtn = document.getElementById('infFlowResume');
    const stopBtn = document.getElementById('infFlowStop');
    const prepBtn = document.getElementById('infFlowPrepare');
    if (runBtn) runBtn.disabled = !!state.running;
    if (pauseBtn) pauseBtn.disabled = !state.running || state.paused;
    if (resumeBtn) resumeBtn.disabled = !state.running || !state.paused;
    if (stopBtn) stopBtn.disabled = !state.running;
    if (prepBtn) prepBtn.disabled = !!state.running || !hasSucceededModules();
    window.__flowIsRunning = !!state.running;
    if (typeof window.__syncFlowLoopMutex === 'function') {
      window.__syncFlowLoopMutex();
    }
  }

  function normalizeTermCond(tc) {
    if (!tc || typeof tc !== 'object') return null;
    if (tc.direction === 'z_rise' || tc.direction === 'z_fall') {
      const th = tc.threshold;
      if (th == null || !Number.isFinite(Number(th))) return null;
      return { direction: tc.direction, threshold: Number(th) };
    }
    if (tc.z_rise_to != null && Number.isFinite(Number(tc.z_rise_to))) {
      return { direction: 'z_rise', threshold: Number(tc.z_rise_to) };
    }
    if (tc.z_fall_to != null && Number.isFinite(Number(tc.z_fall_to))) {
      return { direction: 'z_fall', threshold: Number(tc.z_fall_to) };
    }
    return null;
  }

  function termCondConfigured(tc) {
    return !!normalizeTermCond(tc);
  }

  function termCondTriggered(z, tc) {
    const n = normalizeTermCond(tc);
    if (z == null || !Number.isFinite(Number(z)) || !n) return false;
    const zf = Number(z);
    if (n.direction === 'z_rise') return zf >= n.threshold;
    if (n.direction === 'z_fall') return zf <= n.threshold;
    return false;
  }

  function poseNear(actual, expect, tol) {
    if (!actual || !expect) return { ok: false, why: 'missing_pose' };
    const got = actual.xyzrpy;
    const want = expect.xyzrpy;
    if (!got || !want || got.length < 6 || want.length < 6) return { ok: false, why: 'missing_pose' };
    const posTol = Number((tol && tol.pos_m) != null ? tol.pos_m : 0.01);
    const rotTol = Number((tol && tol.rot_rad) != null ? tol.rot_rad : 0.05);
    const gripTol = Number((tol && tol.grip) != null ? tol.grip : 0.05);
    let dp = 0;
    for (let i = 0; i < 3; i++) dp += (got[i] - want[i]) * (got[i] - want[i]);
    dp = Math.sqrt(dp);
    if (dp > posTol) return { ok: false, why: 'pos' };
    let dr = 0;
    for (let i = 3; i < 6; i++) dr += (got[i] - want[i]) * (got[i] - want[i]);
    dr = Math.sqrt(dr);
    if (dr > rotTol) return { ok: false, why: 'rot' };
    const ag = actual.gripper;
    if (ag == null || !Number.isFinite(Number(ag))) return { ok: false, why: 'missing_grip' };
    if (Math.abs(Number(ag) - Number(expect.gripper || 0)) > gripTol) return { ok: false, why: 'grip' };
    return { ok: true, why: null };
  }

  async function checkStart(mod) {
    setMod(mod.id, 'checking_start');
    if (typeof window.__flowReadPose7 !== 'function') {
      return { ok: false, error: 'no_read_helper' };
    }
    const cur = window.__flowReadPose7();
    const near = poseNear(cur, mod.start, mod.start_tol || {});
    if (!near.ok) {
      const err = 'start_pose_mismatch:' + (near.why || 'unknown');
      setMod(mod.id, 'failed', err);
      return { ok: false, error: err, actual: cur };
    }
    return { ok: true };
  }

  async function waitWhileFlowPaused(gen) {
    while (state.running && gen === state.gen && state.paused) {
      await sleepMs(100);
    }
    return state.running && gen === state.gen;
  }

  const FLOW_TRAIL_COLORS = {
    program: '#3dd6c6',
    infer: '#fbbf24',
    fixed_program: '#38bdf8',
  };
  let savedTrailColorCss = null;

  function flowModeKey(mod) {
    if (!mod) return 'program';
    if (mod.type === 'pose_check') return 'fixed_program';
    if (mod.motion_mode === 'infer') return 'infer';
    return 'program';
  }

  function beginModuleTrailColor(mod) {
    if (typeof window.__setInfPoseTrailColor !== 'function') return;
    if (savedTrailColorCss == null) {
      savedTrailColorCss = (typeof window.__getInfPoseTrailColor === 'function')
        ? window.__getInfPoseTrailColor()
        : null;
    }
    const css = FLOW_TRAIL_COLORS[flowModeKey(mod)] || FLOW_TRAIL_COLORS.program;
    window.__setInfPoseTrailColor(css, { persist: false });
  }

  function endFlowTrailColor() {
    if (savedTrailColorCss != null && typeof window.__setInfPoseTrailColor === 'function') {
      try {
        window.__setInfPoseTrailColor(savedTrailColorCss, { persist: true });
      } catch (_) {}
    }
    savedTrailColorCss = null;
  }

  function markGoalOnTrail(mod) {
    if (!mod || !mod.goal || !Array.isArray(mod.goal.xyzrpy)) return;
    if (typeof window.__setInfPoseGoal !== 'function') return;
    try { window.__setInfPoseGoal(mod.goal.xyzrpy); } catch (_) {}
  }

  async function moveToGoal(mod, gen, opts) {
    opts = opts || {};
    if (typeof window.__flowIk !== 'function' || typeof window.__flowSendAbs !== 'function') {
      const err = 'missing_flow_adapters';
      setMod(mod.id, 'failed', err);
      return { ok: false, error: err };
    }
    if (!mod.goal || !mod.goal.xyzrpy) {
      const err = 'missing_goal';
      setMod(mod.id, 'failed', err);
      return { ok: false, error: err };
    }

    // Bake a waypoint in the current mode pen color before / with the move.
    markGoalOnTrail(mod);

    const ikGoal = await window.__flowIk(mod.goal.xyzrpy);
    if (!ikGoal || !ikGoal.ok || !Array.isArray(ikGoal.joints_rad)) {
      const err = 'ik_goal_fail:' + ((ikGoal && ikGoal.error) || 'ik');
      setMod(mod.id, 'failed', err);
      return { ok: false, error: err };
    }

    if (!(await waitWhileFlowPaused(gen))) return { ok: false, stopped: true };

    const sendOpts = {};
    if (mod.goal.gripper != null && Number.isFinite(Number(mod.goal.gripper))) {
      sendOpts.gripper_position_norm = Number(mod.goal.gripper);
    }
    const send = await window.__flowSendAbs(ikGoal.joints_rad, mod.timing || {}, sendOpts);
    if (!send || !send.ok) {
      const err = 'abs_fail:' + ((send && send.error) || 'send');
      setMod(mod.id, 'failed', err);
      return { ok: false, error: err };
    }
    if (typeof window.__flowWaitArrive === 'function') {
      const arr = await window.__flowWaitArrive(gen, send.duration_s, {
        isActiveFn: () => state.running && gen === state.gen,
        isPausedFn: () => state.paused,
      });
      if (!arr || !arr.ok) {
        if (arr && arr.stopped) return { ok: false, stopped: true };
        const err = 'arrive_fail:' + ((arr && arr.error) || 'timeout');
        setMod(mod.id, 'failed', err);
        return { ok: false, error: err };
      }
    }
    return { ok: true };
  }

  /** Program basic: start gate → move to goal (goal grip on last jerk point). */
  async function runProgramModule(mod, gen) {
    beginModuleTrailColor(mod);
    setMod(mod.id, 'running');
    const gate = await checkStart(mod);
    if (!gate.ok) return { ok: false, error: gate.error };
    if (!(await waitWhileFlowPaused(gen))) return { ok: false, stopped: true };

    const r = await moveToGoal(mod, gen, {});
    if (!r.ok) return r;
    setMod(mod.id, 'succeeded');
    return { ok: true };
  }

  /** Pose-check: no start gate; from current pose → goal (program only). */
  async function runPoseCheckModule(mod, gen) {
    beginModuleTrailColor(mod);
    setMod(mod.id, 'running');
    if (!(await waitWhileFlowPaused(gen))) return { ok: false, stopped: true };
    const r = await moveToGoal(mod, gen, {});
    if (!r.ok) return r;
    setMod(mod.id, 'succeeded');
    return { ok: true };
  }

  /**
   * Infer basic: start gate → pi05 step loop until term_cond (dir+threshold).
   * On trigger: module succeeds immediately (no goal / no loop-end pause).
   */
  async function runInferModule(mod, gen) {
    beginModuleTrailColor(mod);
    setMod(mod.id, 'running');
    const gate = await checkStart(mod);
    if (!gate.ok) return { ok: false, error: gate.error };

    if (typeof window.__flowPi05Connected === 'function' && !window.__flowPi05Connected()) {
      const err = 'serve_offline';
      setMod(mod.id, 'failed', err);
      return { ok: false, error: err };
    }
    if (!termCondConfigured(mod.term_cond)) {
      const err = 'missing_term_cond';
      setMod(mod.id, 'failed', err);
      return { ok: false, error: err };
    }
    if (typeof window.__flowPi05Step !== 'function' || typeof window.__flowSendAbs !== 'function') {
      const err = 'missing_flow_adapters';
      setMod(mod.id, 'failed', err);
      return { ok: false, error: err };
    }

    let stepN = 0;
    while (state.running && gen === state.gen) {
      if (!(await waitWhileFlowPaused(gen))) return { ok: false, stopped: true };

      const chunk = (typeof window.__flowChunkSkip === 'function') ? window.__flowChunkSkip() : 1;
      let last = null;
      for (let i = 0; i < chunk; i++) {
        if (!(state.running && gen === state.gen)) return { ok: false, stopped: true };
        if (!(await waitWhileFlowPaused(gen))) return { ok: false, stopped: true };
        last = await window.__flowPi05Step();
        stepN += 1;
        if (!last || last.ok === false) {
          const err = 'step_fail:' + ((last && last.error) || 'step');
          setMod(mod.id, 'failed', err);
          return { ok: false, error: err };
        }
        if (last.term_flag || last.reject_flag) {
          const err = last.reject_flag ? 'reject_flag' : 'term_flag';
          setMod(mod.id, 'failed', err);
          return { ok: false, error: err };
        }
      }

      let joints = null;
      if (last && Array.isArray(last.next_joints_rad) && last.next_joints_rad.length >= 6) {
        joints = last.next_joints_rad.slice(0, 6).map(Number);
      } else if (typeof window.__flowLastStepJoints === 'function') {
        joints = window.__flowLastStepJoints();
      }
      if (!joints) {
        const err = 'no_joints_from_step';
        setMod(mod.id, 'failed', err);
        return { ok: false, error: err };
      }

      const send = await window.__flowSendAbs(joints, mod.timing || {});
      if (!send || !send.ok) {
        const err = 'abs_fail:' + ((send && send.error) || 'send');
        setMod(mod.id, 'failed', err);
        return { ok: false, error: err };
      }
      if (typeof window.__flowWaitArrive === 'function') {
        const arr = await window.__flowWaitArrive(gen, send.duration_s, {
          isActiveFn: () => state.running && gen === state.gen,
          isPausedFn: () => state.paused,
        });
        if (!arr || !arr.ok) {
          if (arr && arr.stopped) return { ok: false, stopped: true };
          const err = 'arrive_fail:' + ((arr && arr.error) || 'timeout');
          setMod(mod.id, 'failed', err);
          return { ok: false, error: err };
        }
      }

      const cur = (typeof window.__flowReadPose7 === 'function') ? window.__flowReadPose7() : null;
      const z = cur && cur.xyzrpy ? cur.xyzrpy[2] : null;
      if (termCondTriggered(z, mod.term_cond)) {
        setMod(mod.id, 'succeeded', t('infer.flow_term_triggered'));
        setHint(t('infer.flow_hint_term_done', { n: stepN }));
        return { ok: true };
      }
      setHint(t('infer.flow_hint_infer_step', { n: stepN }));
    }
    return { ok: false, stopped: true };
  }

  function nextId(graph, fromId) {
    const e = (graph.edges || []).find((x) => x.from === fromId);
    return e ? e.to : null;
  }

  async function runGraph() {
    if (state.running) return;
    if (typeof window.__pi05LoopIsRunning === 'function' && window.__pi05LoopIsRunning()) {
      setHint(t('infer.flow_mutex_loop'));
      return;
    }
    const editor = ed();
    if (!editor) return;
    const graph = editor.getGraph();
    const v = editor.validateChain(graph.modules, graph.edges);
    if (!v.ok) {
      setHint(t('infer.flow_warn_' + v.error) || v.error);
      return;
    }
    state.running = true;
    state.paused = false;
    state.gen += 1;
    const gen = state.gen;
    editor.resetModuleStates();
    startFlowTimer();
    syncToolbar();
    setHint(t('infer.flow_hint_running'));

    let cur = v.entry;
    while (cur && state.running && gen === state.gen) {
      const mod = (graph.modules || []).find((m) => m.id === cur);
      if (!mod) break;
      state.currentModuleId = cur;
      let r;
      if (mod.type === 'pose_check') {
        r = await runPoseCheckModule(mod, gen);
      } else if (mod.motion_mode === 'infer') {
        r = await runInferModule(mod, gen);
      } else {
        r = await runProgramModule(mod, gen);
      }
      if (!r || !r.ok) {
        if (!(r && r.stopped)) {
          setHint(t('infer.flow_hint_failed', { error: (r && r.error) || 'failed' }));
        }
        break;
      }
      cur = nextId(graph, cur);
      if (!cur) {
        const elapsed = stopFlowTimer();
        setHint(t('infer.flow_hint_done') + ' · ' + t('infer.elapsed', { t: formatElapsedMs(elapsed) }));
        break;
      }
    }

    if (state.gen === gen) {
      if (state.timerTick || state.timerStartedAt != null) {
        const elapsed = stopFlowTimer();
        const hintEl = document.getElementById('infFlowHint');
        const curHint = (hintEl && hintEl.textContent) || '';
        if (hintEl && curHint && curHint.indexOf(formatElapsedMs(elapsed)) < 0) {
          hintEl.textContent = curHint + ' · ' + t('infer.elapsed', { t: formatElapsedMs(elapsed) });
        }
      }
      state.running = false;
      state.paused = false;
      state.currentModuleId = null;
      endFlowTrailColor();
      syncToolbar();
    }
  }

  function pauseFlow() {
    if (!state.running || state.paused) return;
    state.paused = true;
    pauseFlowTimer();
    syncToolbar();
    setHint(t('infer.flow_hint_paused'));
  }

  function resumeFlow() {
    if (!state.running || !state.paused) return;
    state.paused = false;
    resumeFlowTimer();
    syncToolbar();
    setHint(t('infer.flow_hint_resumed'));
  }

  async function stopFlow() {
    if (!state.running) return;
    state.running = false;
    state.paused = false;
    state.gen += 1;
    state.currentModuleId = null;
    const elapsed = stopFlowTimer();
    endFlowTrailColor();
    syncToolbar();
    if (typeof window.__flowCancelAbs === 'function') {
      try { await window.__flowCancelAbs(); } catch (_) {}
    }
    setHint(t('infer.flow_hint_stopped') + ' · ' + t('infer.elapsed', { t: formatElapsedMs(elapsed) }));
  }

  function prepareFlow() {
    if (state.running) return;
    const editor = ed();
    if (!editor || !editor.clearSucceededStates) return;
    editor.clearSucceededStates();
    syncToolbar();
    setHint(t('infer.flow_hint_prepared'));
  }

  function init() {
    const runBtn = document.getElementById('infFlowRun');
    const pauseBtn = document.getElementById('infFlowPause');
    const resumeBtn = document.getElementById('infFlowResume');
    const stopBtn = document.getElementById('infFlowStop');
    const prepBtn = document.getElementById('infFlowPrepare');
    if (runBtn) runBtn.addEventListener('click', () => { runGraph(); });
    if (pauseBtn) pauseBtn.addEventListener('click', pauseFlow);
    if (resumeBtn) resumeBtn.addEventListener('click', resumeFlow);
    if (stopBtn) stopBtn.addEventListener('click', () => { stopFlow(); });
    if (prepBtn) prepBtn.addEventListener('click', prepareFlow);
    syncToolbar();
    renderFlowElapsed();
  }

  window.FlowRuntime = {
    init: init,
    run: runGraph,
    pause: pauseFlow,
    resume: resumeFlow,
    stop: stopFlow,
    prepare: prepareFlow,
    syncToolbar: syncToolbar,
    isRunning: () => !!state.running,
  };
  window.__flowIsRunning = false;

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', init);
  } else {
    init();
  }
})();
