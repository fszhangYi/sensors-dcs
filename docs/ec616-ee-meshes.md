# EC616 末端数模（KWR75 + AG95）

> 状态：**已实现**（Three.js / URDF 可视化）  
> 日期：2026-09-09  
> 资源目录：`src/sensors_dcs/static/models/ec616/`  
> 加载入口：`viz.py` → `EC616_URDF_URL`（`/assets/models/ec616/ec616.urdf?v=…`）

---

## 1. 做什么

推理页姿态画布上的 EC616 URDF 末端，由原来的简易 palm + 双指，改为与工站一致的堆叠：

```
Link6（法兰）
  └── ft_kwr75_link           ← meshes/KWR75.STL   （六维力，厚 33.5 mm）
        └── gripper_centor_link ← meshes/AG95.STL （DH AG-95 整机实体，静态）
```

- **只影响数模显示**（`setJointValue` / STL），**不会**下发 Arm / Home / 夹爪指令。  
- AG95 STEP 为整机实体，**无数模开合动画**；夹爪 `position_norm` 只在状态卡显示，不驱动 Three.js。  
- 全精度网格较大时，姿态画布会显示加载动画与进度（`LoadingManager` 按 URDF/STL 条目计数）。  
- 动力学用的 TCP 偏置（默认 `z=0.18`）仍是独立配置，不随本数模自动改写。

---

## 2. 坐标系与安装尺寸

| 环节 | 关节 origin（相对父 link） | 说明 |
|------|---------------------------|------|
| `ft_kwr75_fixed` | `xyz="0 0 0"` | 力传感器机器人侧安装面贴 Link6 法兰，工具轴 = 法兰 `+Z` |
| `gripper_centor_fixed` | `xyz="0 0 0.0335"` | AG95 安装面贴 KWR75 工具侧；**0.0335 m = 33.5 mm**（STEP 文件名 `H=33.5`） |

源文件（数据盘，未入库）：

- `/root/autodl-tmp/KWR75.stp`（或部署机上的同名 STEP）
- `/root/autodl-tmp/AG95.STEP`

转换流程概要：

1. STEP → 三角网格（OpenCASCADE / `cascadio`）  
2. 毫米 → 米；把安装面平移到局部原点，工具轴对齐法兰 `+Z`  
3. 写入 `meshes/*.STL`，由 `ec616.urdf` 引用  

AG95：CAD 长轴原为 `+Y`，经 `Rx(+90°)` 映射到法兰 `+Z`；开合方向大致沿法兰 `±X`（仅几何姿态，无数模关节）。

---

## 3. 为什么臂先出来、力/夹爪很慢（以及怎么减面）

URDFLoader **按链路顺序**拉 STL：`base → Link1…Link6 → KWR75 → AG95`。末端两件排在最后，且原 STEP 细 tessellation 面数很高：

| 网格 | 全精度（当前入库） | 曾减面版本（已不用） |
|------|-------------------|---------------------|
| `AG95.STL` | **~65k / ~3.1 MB** | ~8k / ~0.39 MB |
| `KWR75.STL` | **~27k / ~1.3 MB** | ~6k / ~0.29 MB |
| 六轴连杆合计 | ~143k | 未改 |

当前 Three.js 加载的是 **全精度** 末端网格（便于看清外形）。面数高时末端会比六轴连杆更晚出现，属正常现象。

曾用 `pyfqmr` 做过可视化减面；若需再减面，可从对齐后的全精度 STL 重新简化，并 bump URL 版本号。

改完 STL / URDF 后，请 bump `viz.py` 里的：

```js
const EC616_URDF_URL = '/assets/models/ec616/ec616.urdf?v=ee-full-1';
```

否则浏览器会继续用缓存的旧 URDF / 网格。

---

## 4. 本地核对

```bash
# 面数 / 体积
python3 - <<'PY'
from pathlib import Path
from stl import mesh
d = Path('src/sensors_dcs/static/models/ec616/meshes')
for p in sorted(d.glob('*.STL')):
    m = mesh.Mesh.from_file(str(p))
    print(f'{p.name:16} tris={len(m.vectors):6d}  {p.stat().st_size/1024:7.1f} KB')
PY

# 服务起来后
curl -sI 'http://127.0.0.1:6008/assets/models/ec616/meshes/AG95.STL' | head
curl -s 'http://127.0.0.1:6008/assets/models/ec616/ec616.urdf?v=ee-full-1' | rg 'ft_kwr75|AG95|0.0335'
```

硬刷新页面（Ctrl+Shift+R）后再看姿态画布。

---

## 5. 非目标

- 不驱动真实 KWR75 / AG95 硬件读写  
- **不做**夹爪开合关节动画（AG95 STEP 为整机实体；曾尝试示意垫块，已撤回）  
- 不自动修改 `DEFAULT_TCP_XYZ` / `--tcp-z`（力传感器加厚后若要对齐真 TCP，需单独改配置）
