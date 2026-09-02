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
        help="downsample master timeline to this hz (asof/nearest only; e.g. gello 50Hz -> 15Hz)",
    )
    p_export.add_argument(
        "--format",
        choices=["parquet", "csv", "both"],
        default="parquet",
        dest="export_format",
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
                fmt=args.export_format,
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
            )
            if do_hik:
                from sensors_dcs.export.hik_dataset import export_hik_dataset

                hik_meta = export_hik_dataset(
                    args.episode,
                    output_dir=args.hik_dataset,
                    camera_map_yaml=args.camera_map,
                    robot_name="elite",
                    tcp_z=0.18,
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
            )
        except Exception as e:  # noqa: BLE001
            print(json.dumps({"ok": False, "error": str(e)}, ensure_ascii=False, indent=2))
            return 1
        print(json.dumps({"ok": True, **meta}, ensure_ascii=False, indent=2))
        return 0

    return 1


if __name__ == "__main__":
    raise SystemExit(main())
