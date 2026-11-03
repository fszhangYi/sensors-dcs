from __future__ import annotations

import argparse
import json
import traceback

from sensors_dcs.paths import ensure_sensors_import

ensure_sensors_import()

from sensors_dcs.config import load_dcs_config  # noqa: E402
from sensors_dcs.runtime import Orchestrator  # noqa: E402
from sensors_dcs.ui_serve import pick_port, serve_app_blocking  # noqa: E402
from sensors_dcs.viz import create_error_app  # noqa: E402


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="sensors-dcs", description="sensors-dcs collection runtime")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_run = sub.add_parser("run", help="start agents + viz preview")
    p_run.add_argument("-c", "--config", required=True, help="DCS YAML path")
    p_run.add_argument(
        "--dry-run",
        action="store_true",
        default=False,
        help="force dry_run (overrides YAML)",
    )
    p_run.add_argument(
        "--no-dry-run",
        action="store_true",
        default=False,
        help="force real hardware (overrides YAML)",
    )

    p_show = sub.add_parser("show-config", help="print resolved config JSON")
    p_show.add_argument("-c", "--config", required=True)

    p_export = sub.add_parser(
        "export-timeline",
        help="offline export: merge one episode into unified timeline (CSV/Parquet)",
    )
    p_export.add_argument(
        "-e",
        "--episode",
        required=True,
        help="path to episode directory (episode_XXXXX)",
    )
    p_export.add_argument(
        "-o",
        "--output-dir",
        default=None,
        help="output directory (default: <episode>/export)",
    )
    p_export.add_argument(
        "--align",
        choices=["asof", "nearest", "grid", "union"],
        default=None,
        help="also build aligned wide table (default: events only)",
    )
    p_export.add_argument(
        "--master",
        default=None,
        help="master agent_id for asof/nearest (default: first gello, else top state hz)",
    )
    p_export.add_argument(
        "--hz",
        type=float,
        default=None,
        help="grid frequency for --align grid",
    )
    p_export.add_argument(
        "--master-hz",
        type=float,
        default=None,
        help="downsample master timeline to this hz (asof/nearest only; e.g. gello 50Hz -> 15Hz). "
        "Ignored when --rate-policy is set.",
    )
    p_export.add_argument(
        "--rate-policy",
        default=None,
        help="path to piecewise rate-policy JSON (overrides --master-hz)",
    )
    p_export.add_argument(
        "--align-clock",
        choices=["wall", "hw_ts"],
        default="wall",
        help="master time axis: wall (default) or primary-camera HW color_timestamp",
    )
    p_export.add_argument(
        "--primary-camera",
        default="cam-middle",
        help="camera agent_id for --align-clock hw_ts grid (default: cam-middle)",
    )
    p_export.add_argument(
        "--format",
        choices=["parquet", "csv", "both"],
        default="parquet",
        dest="export_format",
    )
    p_export.add_argument(
        "--allow-invalid",
        action="store_true",
        default=False,
        help="export even when episode manifest.valid=false (作废)",
    )

    p_filter = sub.add_parser(
        "filter-timeline",
        help="filter aligned wide table by match_dt; re-index step from 0",
    )
    p_filter.add_argument("-e", "--episode", required=True, help="episode directory")
    p_filter.add_argument(
        "-i",
        "--input",
        default=None,
        help="aligned table (default: <episode>/export/timeline_aligned.parquet)",
    )
    p_filter.add_argument(
        "-o",
        "--output",
        default=None,
        help="output path (default: <episode>/export/timeline_filtered.parquet)",
    )
    p_filter.add_argument(
        "--require",
        default=None,
        help="required agent ids per row, comma-separated",
    )
    p_filter.add_argument("--master", default=None, help="master agent_id override")
    p_filter.add_argument(
        "--max-match-dt",
        default=None,
        help="max |match_dt| seconds; or cam-left:0.033,gello:0.02",
    )
    p_filter.add_argument(
        "--trim",
        choices=["none", "start", "end", "both"],
        default="both",
    )
    p_filter.add_argument(
        "--materialize",
        action="store_true",
        help="write episode-shaped export/filtered/{manifest.json,states/,cameras/}",
    )
    p_filter.add_argument(
        "--dedupe",
        default=None,
        help="drop consecutive duplicate rows by column(s), e.g. cam-left.image_relpath or cam-left.*",
    )
    p_filter.add_argument(
        "--format",
        choices=["parquet", "csv"],
        default="parquet",
        dest="filter_format",
    )
    p_filter.add_argument(
        "--hik-dataset",
        nargs="?",
        const="export/hik_dataset",
        default=None,
        help="after filter, convert materialize tree to hik_gello postprocess layout "
        "(implies --materialize; optional path, default: export/hik_dataset; "
        "requires --camera-map)",
    )
    p_filter.add_argument(
        "--camera-map",
        default=None,
        help="YAML serial→hik camera name map (required with --hik-dataset; "
        "copied to export/filtered/camera_map.yaml)",
    )
    p_filter.add_argument(
        "--allow-invalid",
        action="store_true",
        default=False,
        help="filter even when episode manifest.valid=false (作废)",
    )

    p_hik = sub.add_parser(
        "export-hik-dataset",
        help="convert export/filtered into hik_gello data_postprocess layout "
        "(metadata.json, steps.json, rgb_*); requires --camera-map unless "
        "export/filtered/camera_map.yaml already exists",
    )
    p_hik.add_argument("-e", "--episode", required=True, help="episode directory")
    p_hik.add_argument(
        "--filtered",
        default=None,
        help="filtered episode root (default: <episode>/export/filtered)",
    )
    p_hik.add_argument(
        "-o",
        "--output-dir",
        default=None,
        help="output directory (default: <episode>/export/hik_dataset)",
    )
    p_hik.add_argument(
        "--camera-map",
        default=None,
        help="YAML serial→hik camera name (e.g. configs/hik_camera_map.yaml). "
        "Optional if export/filtered/camera_map.yaml was written by a prior filter",
    )
    p_hik.add_argument("--robot-name", default="elite", help="metadata.robot")
    p_hik.add_argument("--natural-language", default="", help="metadata.natural_language")
    p_hik.add_argument(
        "--tcp-z",
        type=float,
        default=0.18,
        help="TCP Z offset in meters for FK (default 0.18; unused when FK absent)",
    )
    p_hik.add_argument(
        "--calibration-json",
        default=None,
        help="optional JSON with intrinsic_matrix / extrinsic_matrix for metadata",
    )
    p_hik.add_argument(
        "--no-grid-video",
        action="store_true",
        help="skip writing episode_grid.mp4 (camera grid + sensor panel)",
    )
    p_hik.add_argument(
        "--allow-invalid",
        action="store_true",
        default=False,
        help="export even when episode manifest.valid=false (作废)",
    )

    p_pack = sub.add_parser(
        "pack-hik-datasets",
        help="batch-copy episode_*/export/hik_dataset into {out}/{i}/ "
        "plus video/{i}.mp4 (drops camera_map.yaml from copies)",
    )
    p_pack.add_argument(
        "-i",
        "--input-root",
        required=True,
        help="collect root containing episode_* directories (e.g. D:/data_new)",
    )
    p_pack.add_argument(
        "-o",
        "--output-root",
        required=True,
        help="destination root for {i}/ datasets and video/",
    )
    p_pack.add_argument(
        "--allow-invalid",
        action="store_true",
        default=False,
        help="also pack episodes with manifest.valid=false",
    )

    p_metrics = sub.add_parser(
        "metrics-summary",
        help="E3: aggregate dataset KPIs under a collect root (optional write JSON)",
    )
    p_metrics.add_argument(
        "-c",
        "--root",
        required=True,
        help="collect root containing episode_* (e.g. D:/data or _dcs_data)",
    )
    p_metrics.add_argument(
        "--write",
        action="store_true",
        default=False,
        help="also write <root>/metrics_summary.json",
    )

    p_outcomes = sub.add_parser(
        "outcomes",
        help="summarize task_outcome (E1) + intervention (E2) KPIs under a collect root",
    )
    p_out_sub = p_outcomes.add_subparsers(dest="outcomes_cmd", required=True)
    p_out_sum = p_out_sub.add_parser(
        "summary",
        help="print completion rate / outcome counts for episode_* under root",
    )
    p_out_sum.add_argument(
        "-c",
        "--root",
        required=True,
        help="collect root containing episode_* (e.g. D:/data or _dcs_data)",
    )

    p_std = sub.add_parser(
        "export-standard",
        help="Step 4: convert export/filtered into selected mainstream formats "
        "(lerobot_v21 / lerobot_v30 / hdf5); always force-overwrites output dirs",
    )
    p_std.add_argument("-e", "--episode", required=True, help="episode directory")
    p_std.add_argument(
        "-f",
        "--format",
        dest="formats",
        action="append",
        default=None,
        help="format id (repeatable): lerobot_v21, lerobot_v30, hdf5 "
        "(default: lerobot_v21)",
    )
    p_std.add_argument(
        "--formats",
        dest="formats_csv",
        default=None,
        help="comma-separated formats (alternative to repeated --format)",
    )
    p_std.add_argument(
        "--camera-map",
        default=None,
        help="YAML serial→camera name; optional if export/filtered/camera_map.yaml exists",
    )
    p_std.add_argument("--fps", type=float, default=None, help="override video fps")
    p_std.add_argument(
        "--allow-invalid",
        action="store_true",
        default=False,
        help="export even when episode manifest.valid=false",
    )

    p_std_root = sub.add_parser(
        "export-standard-root",
        help="Step 4 batch: scan episode_* under a dataset root; same as "
        "export-standard per episode (always force overwrite)",
    )
    p_std_root.add_argument(
        "-i",
        "--input-root",
        required=True,
        help="collect root containing episode_* directories",
    )
    p_std_root.add_argument(
        "-f",
        "--format",
        dest="formats",
        action="append",
        default=None,
        help="format id (repeatable); default lerobot_v21",
    )
    p_std_root.add_argument(
        "--formats",
        dest="formats_csv",
        default=None,
        help="comma-separated formats",
    )
    p_std_root.add_argument("--camera-map", default=None)
    p_std_root.add_argument("--fps", type=float, default=None)
    p_std_root.add_argument(
        "--allow-invalid",
        action="store_true",
        default=False,
    )

    p_gather = sub.add_parser(
        "session-gather",
        help="master: Peer-notify slaves to push episode trees into "
        "sessions/<sid>/<slave>/episode_* (resume sync; needs running master viz)",
    )
    p_gather.add_argument(
        "-c",
        "--config",
        default=None,
        help="DCS YAML (reads viz_port / station.gather); default local 7011",
    )
    p_gather.add_argument(
        "--save-dir",
        default=None,
        help="master collect root (default: recorder save_dir on the running master)",
    )
    p_gather.add_argument(
        "--session-id",
        default=None,
        help="linked session id under save_dir/sessions/",
    )
    p_gather.add_argument(
        "-e",
        "--episode",
        default=None,
        help="master episode path (reads session_id / episode_index from manifest)",
    )
    p_gather.add_argument(
        "--station",
        action="append",
        default=None,
        dest="stations",
        help="only gather this slave station_id (repeatable)",
    )
    p_gather.add_argument(
        "--force",
        action="store_true",
        default=False,
        help="re-check / overwrite existing dest files when backend supports it",
    )
    p_gather.add_argument(
        "--viz-url",
        default=None,
        help="override master viz base URL (default http://127.0.0.1:<viz_port>)",
    )
    p_gather.add_argument(
        "--dry-run",
        action="store_true",
        default=False,
        help="resolve session targets / dest_uri only (no Peer, no file transfer)",
    )

    p_batch = sub.add_parser(
        "run-postprocess-root",
        help="batch-run export-timeline → filter-timeline → export-hik-dataset "
        "on every episode_* under a collect root",
    )
    p_batch.add_argument(
        "-i",
        "--input-root",
        required=True,
        help="collect root containing episode_* directories (e.g. D:/data_new)",
    )
    p_batch.add_argument("--align", default="asof", choices=["asof", "nearest", "grid", "union"])
    p_batch.add_argument("--master", default="cam-left")
    p_batch.add_argument("--master-hz", type=float, default=5.0)
    p_batch.add_argument(
        "--rate-policy",
        default=None,
        help="path to piecewise rate-policy JSON (overrides --master-hz)",
    )
    p_batch.add_argument("--align-clock", choices=["wall", "hw_ts"], default="wall")
    p_batch.add_argument("--primary-camera", default="cam-middle")
    p_batch.add_argument(
        "--require",
        default="arm,cam-left,cam-right,cam-middle,gripper-read",
    )
    p_batch.add_argument("--max-match-dt", default="0.033")
    p_batch.add_argument("--trim", default="both", choices=["both", "start", "end", "none"])
    p_batch.add_argument("--no-materialize", action="store_true", default=False)
    p_batch.add_argument(
        "--camera-map",
        required=True,
        help="path to hik_camera_map.yaml",
    )
    p_batch.add_argument(
        "--allow-invalid",
        action="store_true",
        default=False,
        help="also process episodes with manifest.valid=false",
    )
    p_batch.add_argument(
        "--no-overwrite",
        action="store_true",
        default=False,
        help="skip episodes that already have export/ (default: delete export/ then re-run)",
    )

    args = parser.parse_args(argv)

    if args.cmd == "show-config":
        try:
            cfg = load_dcs_config(args.config)
        except Exception as e:  # noqa: BLE001
            print(json.dumps({"ok": False, "error": str(e)}, ensure_ascii=False, indent=2))
            return 1
        from sensors_dcs.config import config_summary

        print(json.dumps(config_summary(cfg), indent=2, ensure_ascii=False))
        return 0

    if args.cmd == "run":
        cfg_path = args.config
        try:
            cfg = load_dcs_config(cfg_path)
            if args.dry_run:
                cfg.dry_run = True
            elif args.no_dry_run:
                cfg.dry_run = False
            orch = Orchestrator(cfg)
        except Exception as e:  # noqa: BLE001
            err = f"{e}\n\n{traceback.format_exc()}"
            print(f"[sensors-dcs] boot error (UI will show details):\n{e}", flush=True)
            port = pick_port(7011)
            app = create_error_app(error=err, config_path=str(cfg_path))
            # CLI run: open browser so the error is visible
            serve_app_blocking(app, host="127.0.0.1", port=port, open_ui=True)
            return 0

        orch.serve()
        return 0

    if args.cmd == "export-timeline":
        try:
            from sensors_dcs.export.timeline import export_episode_timeline
        except ImportError as e:
            print(f"[sensors-dcs] {e}", flush=True)
            return 1
        try:
            meta = export_episode_timeline(
                args.episode,
                output_dir=args.output_dir,
                align=args.align,
                master=args.master,
                hz=args.hz,
                master_hz=args.master_hz,
                rate_policy=getattr(args, "rate_policy", None),
                fmt=args.export_format,
                allow_invalid=bool(getattr(args, "allow_invalid", False)),
                align_clock=getattr(args, "align_clock", "wall") or "wall",
                primary_camera=getattr(args, "primary_camera", None) or "cam-middle",
            )
        except Exception as e:  # noqa: BLE001
            print(json.dumps({"ok": False, "error": str(e)}, ensure_ascii=False, indent=2))
            return 1
        print(json.dumps({"ok": True, **meta}, ensure_ascii=False, indent=2))
        return 0

    if args.cmd == "filter-timeline":
        try:
            from sensors_dcs.export.filter import filter_episode_timeline
        except ImportError as e:
            print(f"[sensors-dcs] {e}", flush=True)
            return 1
        do_hik = getattr(args, "hik_dataset", None) is not None
        try:
            if do_hik and not args.camera_map:
                raise ValueError(
                    "--hik-dataset requires --camera-map <serial→name.yaml> "
                    "(see configs/hik_camera_map.yaml)"
                )
            meta = filter_episode_timeline(
                args.episode,
                input_path=args.input,
                output_path=args.output,
                require=args.require,
                master=args.master,
                max_match_dt=args.max_match_dt,
                trim=args.trim,
                materialize=args.materialize or do_hik,
                dedupe=args.dedupe,
                fmt=args.filter_format,
                allow_invalid=bool(getattr(args, "allow_invalid", False)),
            )
            if do_hik:
                from sensors_dcs.export.hik_dataset import export_hik_dataset

                hik_meta = export_hik_dataset(
                    args.episode,
                    output_dir=args.hik_dataset,
                    camera_map_yaml=args.camera_map,
                    robot_name="elite",
                    tcp_z=0.18,
                    allow_invalid=bool(getattr(args, "allow_invalid", False)),
                )
                meta["hik_dataset"] = hik_meta
        except Exception as e:  # noqa: BLE001
            print(json.dumps({"ok": False, "error": str(e)}, ensure_ascii=False, indent=2))
            return 1
        print(json.dumps({"ok": True, **meta}, ensure_ascii=False, indent=2))
        return 0

    if args.cmd == "export-hik-dataset":
        try:
            from sensors_dcs.export.hik_dataset import export_hik_dataset
        except ImportError as e:
            print(f"[sensors-dcs] {e}", flush=True)
            return 1
        try:
            meta = export_hik_dataset(
                args.episode,
                filtered_dir=args.filtered,
                output_dir=args.output_dir,
                camera_map_yaml=args.camera_map,
                robot_name=args.robot_name,
                natural_language=args.natural_language,
                tcp_z=args.tcp_z,
                calibration_json=args.calibration_json,
                write_grid_video=not args.no_grid_video,
                allow_invalid=bool(getattr(args, "allow_invalid", False)),
            )
        except Exception as e:  # noqa: BLE001
            print(json.dumps({"ok": False, "error": str(e)}, ensure_ascii=False, indent=2))
            return 1
        print(json.dumps({"ok": True, **meta}, ensure_ascii=False, indent=2))
        return 0

    if args.cmd == "pack-hik-datasets":
        try:
            from sensors_dcs.postprocess_service import pack_hik_dataset_root
        except ImportError as e:
            print(f"[sensors-dcs] {e}", flush=True)
            return 1
        result = pack_hik_dataset_root(
            input_root=args.input_root,
            output_root=args.output_root,
            skip_invalid=not bool(getattr(args, "allow_invalid", False)),
        )
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0 if result.get("ok") else 1

    if args.cmd == "metrics-summary":
        from sensors_dcs.metrics.aggregate import aggregate_metrics

        result = aggregate_metrics(args.root, write=bool(getattr(args, "write", False)))
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0 if result.get("ok") else 1

    if args.cmd == "outcomes":
        if getattr(args, "outcomes_cmd", None) == "summary":
            from sensors_dcs.intervention_log import summarize_interventions
            from sensors_dcs.task_outcome import summarize_outcomes

            result = summarize_outcomes(args.root)
            try:
                result["interventions"] = summarize_interventions(args.root)
            except Exception as e:  # noqa: BLE001
                result["interventions"] = {"ok": False, "error": str(e)}
            print(json.dumps(result, ensure_ascii=False, indent=2))
            return 0 if result.get("ok") else 1
        print("[sensors-dcs] unknown outcomes subcommand", flush=True)
        return 1

    if args.cmd == "session-gather":
        sid = getattr(args, "session_id", None)
        ep = getattr(args, "episode", None)
        if not sid and not ep:
            print(
                json.dumps(
                    {"ok": False, "error": "need --session-id and/or --episode"},
                    ensure_ascii=False,
                    indent=2,
                )
            )
            return 1
        save_dir = getattr(args, "save_dir", None)
        publish = ""
        viz_port = 7011
        if args.config:
            try:
                cfg = load_dcs_config(args.config)
            except Exception as e:  # noqa: BLE001
                print(json.dumps({"ok": False, "error": str(e)}, ensure_ascii=False, indent=2))
                return 1
            viz_port = int(cfg.runtime.viz_port)
            if not save_dir:
                save_dir = cfg.record.save_dir
            publish = (cfg.station.gather.publish_uri or "").strip() or (
                cfg.station.share_root or ""
            ).strip()
        if getattr(args, "dry_run", False):
            from sensors_dcs.session_gather import (
                build_gather_dest,
                resolve_session_for_gather,
            )

            try:
                resolved = resolve_session_for_gather(
                    save_dir=save_dir or "./data",
                    session_id=sid,
                    episode_path=ep,
                )
            except Exception as e:  # noqa: BLE001
                print(json.dumps({"ok": False, "error": str(e)}, ensure_ascii=False, indent=2))
                return 1
            want = {str(x).strip() for x in (args.stations or []) if str(x).strip()}
            targets = []
            for p in resolved["doc"].get("participants") or []:
                if not isinstance(p, dict):
                    continue
                if str(p.get("role") or "").strip().lower() != "slave":
                    continue
                st_id = str(p.get("station_id") or "").strip()
                if not st_id or (want and st_id not in want):
                    continue
                targets.append(
                    build_gather_dest(
                        save_dir=resolved["save_dir"],
                        session_id=resolved["session_id"],
                        station_id=st_id,
                        episode_index=int(resolved["episode_index"]),
                        publish_uri=publish,
                    )
                    | {"station_id": st_id}
                )
            print(
                json.dumps(
                    {
                        "ok": True,
                        "dry_run": True,
                        "session_id": resolved["session_id"],
                        "episode_index": resolved["episode_index"],
                        "publish_uri": publish or None,
                        "targets": targets,
                    },
                    ensure_ascii=False,
                    indent=2,
                )
            )
            return 0

        import urllib.error
        import urllib.request

        base = (getattr(args, "viz_url", None) or "").strip().rstrip("/")
        if not base:
            base = f"http://127.0.0.1:{viz_port}"
        payload = {
            "session_id": sid,
            "episode": ep,
            "save_dir": save_dir,
            "station_ids": list(args.stations or []) or None,
            "force": bool(getattr(args, "force", False)),
        }
        req = urllib.request.Request(
            f"{base}/api/postprocess/session/gather",
            data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=7200) as resp:
                body = resp.read().decode("utf-8", errors="replace")
        except urllib.error.HTTPError as e:
            body = e.read().decode("utf-8", errors="replace") if e.fp else str(e)
            try:
                result = json.loads(body) if body else {"ok": False, "error": str(e)}
            except json.JSONDecodeError:
                result = {"ok": False, "error": body or str(e)}
            print(json.dumps(result, ensure_ascii=False, indent=2))
            return 1
        except Exception as e:  # noqa: BLE001
            print(
                json.dumps(
                    {
                        "ok": False,
                        "error": f"{type(e).__name__}: {e}",
                        "hint": "start master with sensors-dcs run -c … first",
                    },
                    ensure_ascii=False,
                    indent=2,
                )
            )
            return 1
        try:
            result = json.loads(body) if body else {"ok": False, "error": "empty response"}
        except json.JSONDecodeError:
            result = {"ok": False, "error": body[:800]}
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0 if result.get("ok") else 1

    if args.cmd == "run-postprocess-root":
        try:
            from sensors_dcs.postprocess_service import run_postprocess_root
        except ImportError as e:
            print(f"[sensors-dcs] {e}", flush=True)
            return 1
        result = run_postprocess_root(
            input_root=args.input_root,
            align=args.align,
            master=args.master,
            master_hz=args.master_hz,
            rate_policy=getattr(args, "rate_policy", None),
            align_clock=args.align_clock,
            primary_camera=args.primary_camera,
            require=args.require,
            max_match_dt=args.max_match_dt,
            trim=args.trim,
            materialize=not bool(getattr(args, "no_materialize", False)),
            camera_map=args.camera_map,
            allow_invalid=bool(getattr(args, "allow_invalid", False)),
            overwrite=not bool(getattr(args, "no_overwrite", False)),
        )
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0 if result.get("ok") else 1

    if args.cmd in {"export-standard", "export-standard-root"}:
        formats = list(getattr(args, "formats", None) or [])
        csv = getattr(args, "formats_csv", None)
        if csv:
            formats.extend([p.strip() for p in str(csv).split(",") if p.strip()])
        if not formats:
            formats = ["lerobot_v21"]
        try:
            from sensors_dcs.postprocess_service import (
                run_standard_export,
                run_standard_export_root,
            )
        except ImportError as e:
            print(f"[sensors-dcs] {e}", flush=True)
            return 1
        if args.cmd == "export-standard":
            result = run_standard_export(
                episode=args.episode,
                formats=formats,
                camera_map=args.camera_map,
                allow_invalid=bool(getattr(args, "allow_invalid", False)),
                fps=getattr(args, "fps", None),
            )
        else:
            result = run_standard_export_root(
                input_root=args.input_root,
                formats=formats,
                camera_map=args.camera_map,
                allow_invalid=bool(getattr(args, "allow_invalid", False)),
                fps=getattr(args, "fps", None),
            )
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0 if result.get("ok") else 1

    return 1


if __name__ == "__main__":
    raise SystemExit(main())
