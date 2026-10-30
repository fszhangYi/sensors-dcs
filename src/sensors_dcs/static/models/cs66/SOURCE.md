# CS66 model assets

- Local archive: `sensors-arm/CS66urdf.rar` → extracted package `CS66urdf/`
- **Meshes (arm)**: SolidWorks export (unchanged)
- **Joint origins** (2026-10-15): rebuilt from `sensors.cs66_kinematics` profile
  `elite_cs66` teach MDH so URDF flange FK matches MDH FK
- Control SDK: **Elite CS** — hik-sensors `arm_cs_write` / `arm_cs_read` + `elite_cs_sdk`
  (not EC `elirobots` / `arm_write`)
- Note: exporter wrote `<limit lower="0" upper="0"/>` on all revolute joints, which
  clamps URDFLoader `setJointValue` to zero. Limits were widened to ±2π for viz.
- **EE (viz)**:
  - KWR75 FT on `link6` (same as Cell A)
  - **EFG-C65** gripper (replaces AG-95): source STEP `cad/EFG-C65.STEP`
    (from `EFG-C65（外发）.STEP`); runtime mesh `meshes/EFG-C65.STL`
    (Rx+90° so CAD −Y mount → tool −Z; mm→m; mount bore axis@XY=0, z=0; tip ≈ z=0.117)
  - Mount joint: gripper at z=+0.0335 on KWR75 tool face
  - Cell B `tcp_xyz` default `[0,0,0.1505]` ≈ flange→EFG tip
