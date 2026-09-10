/**
 * Elite EC616 TCP IK (browser) — mirrors sensors.kinematics + sensors_dcs.arm_pose.
 * Machine joints (rad) → standard DH flange FK; TCP = flange @ Transl(0,0,0.18).
 * Solver: damped Gauss–Newton on SE(3) residual (same weighting as ik_flange).
 */
(function (global) {
  'use strict';

  const PI2 = Math.PI / 2;
  // LINK_LENGTH_MM / 1000 → d1,a2,a3,d4,d5,d6
  const L = [0.182, 0.478359, 0.361183, 0.174159, 0.116439, 0.109807];
  // DH rows: (theta_offset, d, a, alpha) — theta_offset always 0 (machine_deg ≡ DH q)
  const DH = [
    [0, L[0], 0, -PI2],
    [0, 0, L[1], 0],
    [0, 0, L[2], 0],
    [0, L[3], 0, -PI2],
    [0, L[4], 0, -PI2],
    [0, L[5], 0, 0],
  ];
  const TCP_Z = 0.18;
  const SOFT_MIN = [-Math.PI * 2, -Math.PI * 2, (-156 * Math.PI) / 180, -Math.PI * 2, -Math.PI * 2, -Math.PI * 2];
  const SOFT_MAX = [Math.PI * 2, Math.PI * 2, (156 * Math.PI) / 180, Math.PI * 2, Math.PI * 2, Math.PI * 2];

  function mat4Identity() {
    return [1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1];
  }

  /** Column-major 4x4 multiply: out = a @ b (row-vector convention matching numpy row-major layout stored row-wise). */
  function mat4Mul(a, b) {
    const o = new Array(16);
    for (let r = 0; r < 4; r++) {
      for (let c = 0; c < 4; c++) {
        o[r * 4 + c] =
          a[r * 4 + 0] * b[0 * 4 + c] +
          a[r * 4 + 1] * b[1 * 4 + c] +
          a[r * 4 + 2] * b[2 * 4 + c] +
          a[r * 4 + 3] * b[3 * 4 + c];
      }
    }
    return o;
  }

  function standardDhLink(theta, d, a, alpha) {
    const ct = Math.cos(theta);
    const st = Math.sin(theta);
    const ca = Math.cos(alpha);
    const sa = Math.sin(alpha);
    // row-major like numpy
    return [
      ct, -st * ca, st * sa, a * ct,
      st, ct * ca, -ct * sa, a * st,
      0, sa, ca, d,
      0, 0, 0, 1,
    ];
  }

  function fkFlange(q) {
    let T = mat4Identity();
    for (let i = 0; i < 6; i++) {
      const row = DH[i];
      T = mat4Mul(T, standardDhLink(row[0] + q[i], row[1], row[2], row[3]));
    }
    return T;
  }

  function tcpOffsetMat() {
    return [1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1, TCP_Z, 0, 0, 0, 1];
  }

  function invTcpOffsetMat() {
    return [1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1, -TCP_Z, 0, 0, 0, 1];
  }

  function xyzrpyToMat(xyzrpy) {
    const x = +xyzrpy[0];
    const y = +xyzrpy[1];
    const z = +xyzrpy[2];
    const rx = +xyzrpy[3];
    const ry = +xyzrpy[4];
    const rz = +xyzrpy[5];
    const cx = Math.cos(rx);
    const sx = Math.sin(rx);
    const cy = Math.cos(ry);
    const sy = Math.sin(ry);
    const cz = Math.cos(rz);
    const sz = Math.sin(rz);
    // XYZ extrinsic = Rz @ Ry @ Rx (scipy)
    const r00 = cy * cz;
    const r01 = sx * sy * cz - cx * sz;
    const r02 = cx * sy * cz + sx * sz;
    const r10 = cy * sz;
    const r11 = sx * sy * sz + cx * cz;
    const r12 = cx * sy * sz - sx * cz;
    const r20 = -sy;
    const r21 = sx * cy;
    const r22 = cx * cy;
    return [
      r00, r01, r02, x,
      r10, r11, r12, y,
      r20, r21, r22, z,
      0, 0, 0, 1,
    ];
  }

  function rotvecSo3(R) {
    // R is 3x3 row-major flat [r00..r22] extracted from mat
    const tr = R[0] + R[4] + R[8];
    let cosT = (tr - 1) * 0.5;
    if (cosT > 1) cosT = 1;
    if (cosT < -1) cosT = -1;
    const theta = Math.acos(cosT);
    const phi = [
      0.5 * (R[7] - R[5]),
      0.5 * (R[2] - R[6]),
      0.5 * (R[3] - R[1]),
    ];
    if (theta < 1e-9) return [0, 0, 0];
    const st = Math.sin(theta);
    if (Math.abs(st) > 1e-4) {
      const s = theta / st;
      return [phi[0] * s, phi[1] * s, phi[2] * s];
    }
    // near π: use (R+R^T) dominant eigenvector approx via power iteration on diag-dominant
    const S = [
      R[0] + R[0], R[1] + R[3], R[2] + R[6],
      R[3] + R[1], R[4] + R[4], R[5] + R[7],
      R[6] + R[2], R[7] + R[5], R[8] + R[8],
    ];
    let k = [1, 0, 0];
    for (let it = 0; it < 12; it++) {
      const kx = S[0] * k[0] + S[1] * k[1] + S[2] * k[2];
      const ky = S[3] * k[0] + S[4] * k[1] + S[5] * k[2];
      const kz = S[6] * k[0] + S[7] * k[1] + S[8] * k[2];
      const n = Math.hypot(kx, ky, kz) || 1;
      k = [kx / n, ky / n, kz / n];
    }
    return [k[0] * theta, k[1] * theta, k[2] * theta];
  }

  function mat3FromT(T) {
    return [T[0], T[1], T[2], T[4], T[5], T[6], T[8], T[9], T[10]];
  }

  function mat3Mul(A, B) {
    return [
      A[0] * B[0] + A[1] * B[3] + A[2] * B[6],
      A[0] * B[1] + A[1] * B[4] + A[2] * B[7],
      A[0] * B[2] + A[1] * B[5] + A[2] * B[8],
      A[3] * B[0] + A[4] * B[3] + A[5] * B[6],
      A[3] * B[1] + A[4] * B[4] + A[5] * B[7],
      A[3] * B[2] + A[4] * B[5] + A[5] * B[8],
      A[6] * B[0] + A[7] * B[3] + A[8] * B[6],
      A[6] * B[1] + A[7] * B[4] + A[8] * B[7],
      A[6] * B[2] + A[7] * B[5] + A[8] * B[8],
    ];
  }

  function mat3T(A) {
    return [A[0], A[3], A[6], A[1], A[4], A[7], A[2], A[5], A[8]];
  }

  function poseErrorSe3(Tcur, Tdes, oriWeight) {
    const e = new Array(6);
    e[0] = Tdes[3] - Tcur[3];
    e[1] = Tdes[7] - Tcur[7];
    e[2] = Tdes[11] - Tcur[11];
    const Rerr = mat3Mul(mat3FromT(Tdes), mat3T(mat3FromT(Tcur)));
    const rv = rotvecSo3(Rerr);
    e[3] = rv[0] * oriWeight;
    e[4] = rv[1] * oriWeight;
    e[5] = rv[2] * oriWeight;
    return e;
  }

  function norm6(e) {
    return Math.hypot(e[0], e[1], e[2], e[3], e[4], e[5]);
  }

  function clipSoft(q) {
    const o = q.slice();
    for (let i = 0; i < 6; i++) {
      if (o[i] < SOFT_MIN[i]) o[i] = SOFT_MIN[i];
      if (o[i] > SOFT_MAX[i]) o[i] = SOFT_MAX[i];
    }
    return o;
  }

  /** Solve 6x6 SPD system A x = b via Gaussian elimination with partial pivot. */
  function solve6(A, b) {
    const M = A.map((row) => row.slice());
    const x = b.slice();
    for (let i = 0; i < 6; i++) {
      let piv = i;
      for (let r = i + 1; r < 6; r++) {
        if (Math.abs(M[r][i]) > Math.abs(M[piv][i])) piv = r;
      }
      if (Math.abs(M[piv][i]) < 1e-14) return null;
      if (piv !== i) {
        const tmp = M[i];
        M[i] = M[piv];
        M[piv] = tmp;
        const tb = x[i];
        x[i] = x[piv];
        x[piv] = tb;
      }
      const diag = M[i][i];
      for (let c = i; c < 6; c++) M[i][c] /= diag;
      x[i] /= diag;
      for (let r = 0; r < 6; r++) {
        if (r === i) continue;
        const f = M[r][i];
        if (f === 0) continue;
        for (let c = i; c < 6; c++) M[r][c] -= f * M[i][c];
        x[r] -= f * x[i];
      }
    }
    return x;
  }

  function jacobian(q, Tdes, oriWeight, eps) {
    const J = Array.from({ length: 6 }, () => new Array(6));
    const r0 = poseErrorSe3(fkFlange(q), Tdes, oriWeight);
    for (let j = 0; j < 6; j++) {
      const qp = q.slice();
      qp[j] += eps;
      const rp = poseErrorSe3(fkFlange(qp), Tdes, oriWeight);
      for (let i = 0; i < 6; i++) J[i][j] = (rp[i] - r0[i]) / eps;
    }
    return { J, r0 };
  }

  /**
   * @param {number[]} xyzrpy TCP pose [x,y,z,rx,ry,rz]
   * @param {number[]} seedRad machine joints rad length 6
   * @param {object} [opts]
   */
  function xyzrpyToJoints(xyzrpy, seedRad, opts) {
    opts = opts || {};
    const oriWeight = opts.oriWeight != null ? opts.oriWeight : 0.3;
    const posTol = opts.positionToleranceM != null ? opts.positionToleranceM : 2e-3;
    const oriTol = opts.orientationToleranceRad != null ? opts.orientationToleranceRad : 2e-2;
    const acceptResidual = opts.acceptResidual != null ? opts.acceptResidual : 5e-3;
    const maxNfev = opts.maxNfev != null ? opts.maxNfev : 80;
    const enforceSoft = opts.enforceTeachSoftLimits !== false;

    if (!xyzrpy || xyzrpy.length < 6 || !seedRad || seedRad.length < 6) {
      return { ok: false, error: 'xyzrpy/seed need 6 floats', joints_rad: null };
    }
    const Ttcp = xyzrpyToMat(xyzrpy);
    const Tdes = mat4Mul(Ttcp, invTcpOffsetMat());
    let q = seedRad.slice(0, 6).map(Number);
    if (q.some((v) => !Number.isFinite(v))) {
      return { ok: false, error: 'seed non-finite', joints_rad: null };
    }
    if (enforceSoft) q = clipSoft(q);

    let nfev = 0;
    let lambda = 1e-3;
    let bestQ = q.slice();
    let bestR = Infinity;
    const eps = 1e-6;

    while (nfev < maxNfev) {
      const { J, r0 } = jacobian(q, Tdes, oriWeight, eps);
      nfev += 7; // 1 + 6 finite-diff
      const rn = norm6(r0);
      if (rn < bestR) {
        bestR = rn;
        bestQ = q.slice();
      }
      const posErr = Math.hypot(r0[0], r0[1], r0[2]);
      const oriErr = Math.hypot(r0[3], r0[4], r0[5]);
      if (posErr < posTol && oriErr < oriWeight * oriTol) {
        break;
      }
      // (J^T J + λ I) δ = -J^T r
      const JTJ = Array.from({ length: 6 }, () => new Array(6).fill(0));
      const g = new Array(6).fill(0);
      for (let i = 0; i < 6; i++) {
        for (let j = 0; j < 6; j++) {
          let s = 0;
          for (let k = 0; k < 6; k++) s += J[k][i] * J[k][j];
          JTJ[i][j] = s;
        }
        JTJ[i][i] += lambda;
        let gs = 0;
        for (let k = 0; k < 6; k++) gs += J[k][i] * r0[k];
        g[i] = -gs;
      }
      const delta = solve6(JTJ, g);
      if (!delta) break;
      const qNew = q.map((v, i) => v + delta[i]);
      const qTry = enforceSoft ? clipSoft(qNew) : qNew;
      const rTry = poseErrorSe3(fkFlange(qTry), Tdes, oriWeight);
      nfev += 1;
      const rnTry = norm6(rTry);
      if (rnTry < rn) {
        q = qTry;
        lambda = Math.max(1e-7, lambda * 0.3);
        if (norm6(delta) < 1e-9) break;
      } else {
        lambda = Math.min(1e3, lambda * 4);
        if (lambda > 500) {
          q = bestQ;
          break;
        }
      }
    }

    q = bestQ;
    const Tchk = fkFlange(q);
    const e = poseErrorSe3(Tchk, Tdes, oriWeight);
    const residual = norm6(e);
    const posOk = Math.hypot(e[0], e[1], e[2]) < posTol;
    const oriOk = Math.hypot(e[3], e[4], e[5]) < oriWeight * oriTol;
    const ok = posOk && oriOk || residual <= acceptResidual;
    if (!ok) {
      return {
        ok: false,
        error: `IK did not converge residual=${residual.toExponential(3)}`,
        joints_rad: null,
        residual_norm: residual,
        nfev,
      };
    }
    // TCP FK check
    const TtcpAch = mat4Mul(Tchk, tcpOffsetMat());
    const posErrM = Math.hypot(
      TtcpAch[3] - xyzrpy[0],
      TtcpAch[7] - xyzrpy[1],
      TtcpAch[11] - xyzrpy[2],
    );
    if (posErrM > posTol * 2.5) {
      return {
        ok: false,
        error: `IK pose check failed: pos_err=${posErrM.toExponential(3)}m`,
        joints_rad: null,
        residual_norm: residual,
        nfev,
      };
    }
    return {
      ok: true,
      joints_rad: q.slice(0, 6),
      residual_norm: residual,
      nfev,
      ik_success_flag: posOk && oriOk,
      pos_err_m: posErrM,
      source: 'local',
    };
  }

  global.Ec616Ik = {
    xyzrpyToJoints,
    fkFlange,
    TCP_Z,
  };
})(typeof window !== 'undefined' ? window : globalThis);
