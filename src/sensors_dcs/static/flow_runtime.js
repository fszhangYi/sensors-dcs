/**
 * Infer Tab · Flow runtime (P1/P2): program + infer module execution.
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
    goalPause: false, // infer term_cond triggered → waiting for Continue
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

  function syncToolbar() {
    const runBtn = document.getElementById('infFlowRun');
    const pauseBtn = document.getElementById('infFlowPause');
    const resumeBtn = document.getElementById('infFlowResume');
    const stopBtn = document.getElementById('infFlowStop');
    if (runBtn) runBtn.disabled = !!state.running;
    if (pauseBtn) pauseBtn.disabled = !state.running || state.paused;
    if (resumeBtn) resumeBtn.disabled = !state.running || !state.paused;
    if (stopBtn) stopBtn.disabled = !state.running;
    window.__flowIsRunning = !!state.running;
    if (typeof window.__syncFlowLoopMutex === 'function') {
      window.__syncFlowLoopMutex();
    }
  }

  function termCondConfigured(tc) {
    if (!tc) return false;
    const rise = tc.z_rise_to;
    const fall = tc.z_fall_to;
    return (rise != null && Number.isFinite(Number(rise))) ||
      (fall != null && Number.isFinite(Number(fall)));
  }

  function termCondTriggered(z, tc) {
    if (z == null || !Number.isFinite(Number(z)) || !tc) return false;
    const zf = Number(z);
    if (tc.z_rise_to != null && Number.isFinite(Number(tc.z_rise_to)) && zf >= Number(tc.z_rise_to)) {
      return true;
    }
    if (tc.z_fall_to != null && Number.isFinite(Number(tc.z_fall_to)) && zf <= Number(tc.z_fall_to)) {
      return true;
    }
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

  async function runProgramModule(mod, gen) {
    setMod(mod.id, 'running');
    const gate = await checkStart(mod);
    if (!gate.ok) return { ok: false, error: gate.error };
    if (!(await waitWhileFlowPaused(gen))) return { ok: false, stopped: true };

    if (typeof window.__flowIk !== 'function' || typeof window.__flowSendAbs !== 'function') {
      const err = 'missing_flow_adapters';
      setMod(mod.id, 'failed', err);
      return { ok: false, error: err };
    }

    const ikGoal = await window.__flowIk(mod.goal.xyzrpy);
    if (!ikGoal || !ikGoal.ok || !Array.isArray(ikGoal.joints_rad)) {
      const err = 'ik_goal_fail:' + ((ikGoal && ikGoal.error) || 'ik');
      setMod(mod.id, 'failed', err);
      return { ok: false, error: err };
    }

    if (typeof window.__flowGripper === 'function' && mod.start.gripper != null) {
      await window.__flowGripper(mod.start.gripper);
    }
    if (!(await waitWhileFlowPaused(gen))) return { ok: false, stopped: true };

    const send = await window.__flowSendAbs(ikGoal.joints_rad, mod.timing || {});
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
    if (typeof window.__flowGripper === 'function' && mod.goal.gripper != null) {
      await window.__flowGripper(mod.goal.gripper);
    }
    setMod(mod.id, 'succeeded');
    return { ok: true };
  }

  async function runInferModule(mod, gen) {
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

      // User Continue after goal-pause → module succeeded (Q3 default)
      if (state.goalPause && !state.paused) {
        state.goalPause = false;
        setMod(mod.id, 'succeeded');
        return { ok: true, afterGoalPause: true };
      }

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
          // Align with LOOP: treat as stoppable signal but for flow mark failed/pause
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
        // Pretend goal is the last point of current LOOP → auto pause (Q7: no near-goal required)
        state.goalPause = true;
        state.paused = true;
        setMod(mod.id, 'paused', t('infer.flow_term_triggered'));
        setHint(t('infer.flow_hint_term_pause', { n: stepN }));
        syncToolbar();
        // Wait until Continue clears pause; then succeed this module
        while (state.running && gen === state.gen && state.paused) {
          await sleepMs(100);
        }
        if (!(state.running && gen === state.gen)) return { ok: false, stopped: true };
        state.goalPause = false;
        setMod(mod.id, 'succeeded');
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
    state.goalPause = false;
    state.gen += 1;
    const gen = state.gen;
    editor.resetModuleStates();
    syncToolbar();
    setHint(t('infer.flow_hint_running'));

    let cur = v.entry;
    while (cur && state.running && gen === state.gen) {
      const mod = (graph.modules || []).find((m) => m.id === cur);
      if (!mod) break;
      state.currentModuleId = cur;
      let r;
      if (mod.motion_mode === 'infer') {
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
        setHint(t('infer.flow_hint_done'));
        break;
      }
    }

    if (state.gen === gen) {
      state.running = false;
      state.paused = false;
      state.goalPause = false;
      state.currentModuleId = null;
      syncToolbar();
    }
  }

  function pauseFlow() {
    if (!state.running || state.paused) return;
    state.paused = true;
    syncToolbar();
    setHint(t('infer.flow_hint_paused'));
  }

  function resumeFlow() {
    if (!state.running || !state.paused) return;
    state.paused = false;
    syncToolbar();
    setHint(t('infer.flow_hint_resumed'));
  }

  async function stopFlow() {
    if (!state.running) return;
    state.running = false;
    state.paused = false;
    state.goalPause = false;
    state.gen += 1;
    state.currentModuleId = null;
    syncToolbar();
    if (typeof window.__flowCancelAbs === 'function') {
      try { await window.__flowCancelAbs(); } catch (_) {}
    }
    setHint(t('infer.flow_hint_stopped'));
  }

  function init() {
    const runBtn = document.getElementById('infFlowRun');
    const pauseBtn = document.getElementById('infFlowPause');
    const resumeBtn = document.getElementById('infFlowResume');
    const stopBtn = document.getElementById('infFlowStop');
    if (runBtn) runBtn.addEventListener('click', () => { runGraph(); });
    if (pauseBtn) pauseBtn.addEventListener('click', pauseFlow);
    if (resumeBtn) resumeBtn.addEventListener('click', resumeFlow);
    if (stopBtn) stopBtn.addEventListener('click', () => { stopFlow(); });
    syncToolbar();
  }

  window.FlowRuntime = {
    init: init,
    run: runGraph,
    pause: pauseFlow,
    resume: resumeFlow,
    stop: stopFlow,
    isRunning: () => !!state.running,
  };
  window.__flowIsRunning = false;

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', init);
  } else {
    init();
  }
})();
