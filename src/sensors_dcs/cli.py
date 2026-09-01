from __future__ import annotations

import argparse
import json

from sensors_dcs.paths import ensure_sensors_import

ensure_sensors_import()

from sensors_dcs.config import load_dcs_config  # noqa: E402
from sensors_dcs.runtime import Orchestrator  # noqa: E402


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

    args = parser.parse_args(argv)

    if args.cmd == "show-config":
        cfg = load_dcs_config(args.config)
        from sensors_dcs.config import config_summary

        print(json.dumps(config_summary(cfg), indent=2, ensure_ascii=False))
        return 0

    if args.cmd == "run":
        cfg = load_dcs_config(args.config)
        if args.dry_run:
            cfg.dry_run = True
        elif args.no_dry_run:
            cfg.dry_run = False
        orch = Orchestrator(cfg)
        orch.serve()
        return 0

    return 1


if __name__ == "__main__":
    raise SystemExit(main())
