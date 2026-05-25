"""
LHI ExB App Inspector
Script 06: Full Single-App Inspection Runner v0.8.3

Purpose:
- Run the full single-app inspection pipeline:
  01_scan_exb_app_metadata.py
  02_extract_exb_dependencies.py
  03_scan_webmap_layers.py
  04_check_layer_health.py
  05_check_sharing_compatibility.py

- Automatically find the newest output from each stage
- Reduce manual filename mistakes
- Produce a final HTML sharing compatibility report
- Print machine-readable failure stage/error markers for Script 07
- Prompt for username/password once and reuse credentials securely during this run

Author: Lazy Hat Innovations
"""

from __future__ import annotations

import argparse
import getpass
import logging
import os
import re
import shlex
import subprocess
import sys
from dataclasses import dataclass
from datetime import datetime as dt
from pathlib import Path
from typing import List, Optional


# -----------------------------------------------------------------------------
# Defaults
# -----------------------------------------------------------------------------

OUTPUT_ROOT = Path("outputs")
CSV_DIR = OUTPUT_ROOT / "csv"
RAW_JSON_DIR = OUTPUT_ROOT / "raw_json"
LOG_DIR = OUTPUT_ROOT / "logs"
REPORTS_DIR = OUTPUT_ROOT / "reports"

SCRIPT_01 = "01_scan_exb_app_metadata.py"
SCRIPT_02 = "02_extract_exb_dependencies.py"
SCRIPT_03 = "03_scan_webmap_layers.py"
SCRIPT_04 = "04_check_layer_health.py"
SCRIPT_05 = "05_check_sharing_compatibility.py"


# -----------------------------------------------------------------------------
# Data model
# -----------------------------------------------------------------------------

@dataclass
class PipelineOutputs:
    app_summary_csv: Optional[Path] = None
    raw_exb_json: Optional[Path] = None
    exb_extraction_summary_csv: Optional[Path] = None
    widget_inventory_csv: Optional[Path] = None
    data_source_inventory_csv: Optional[Path] = None
    widget_dependencies_csv: Optional[Path] = None
    webmap_references_csv: Optional[Path] = None
    layout_issues_csv: Optional[Path] = None
    webmap_summary_csv: Optional[Path] = None
    webmap_layers_csv: Optional[Path] = None
    webmap_tables_csv: Optional[Path] = None
    exb_resolution_csv: Optional[Path] = None
    layer_health_summary_csv: Optional[Path] = None
    layer_health_details_csv: Optional[Path] = None
    sharing_summary_csv: Optional[Path] = None
    sharing_details_csv: Optional[Path] = None
    sharing_recommendations_csv: Optional[Path] = None
    sharing_html_report: Optional[Path] = None


class PipelineStageError(RuntimeError):
    """Raised when a specific pipeline stage fails."""

    def __init__(self, stage_code: str, stage_name: str, message: str):
        super().__init__(message)
        self.stage_code = stage_code
        self.stage_name = stage_name
        self.message = message


# -----------------------------------------------------------------------------
# Setup/utilities
# -----------------------------------------------------------------------------

def setup_output_dirs() -> None:
    for folder in [OUTPUT_ROOT, CSV_DIR, RAW_JSON_DIR, LOG_DIR, REPORTS_DIR]:
        folder.mkdir(parents=True, exist_ok=True)


def setup_logging() -> Path:
    setup_output_dirs()
    timestamp = dt.now().strftime("%Y%m%d_%H%M%S")
    log_path = LOG_DIR / f"run_full_exb_inspection_{timestamp}.log"

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s | %(levelname)s | %(message)s",
        handlers=[
            logging.FileHandler(log_path, encoding="utf-8"),
            logging.StreamHandler(sys.stdout),
        ],
    )
    return log_path


def require_file(path: Path, label: str) -> None:
    if not path.exists():
        raise FileNotFoundError(f"{label} not found: {path}")


def latest_file(folder: Path, pattern: str, since: Optional[float] = None) -> Path:
    files = list(folder.glob(pattern))

    if since is not None:
        files = [f for f in files if f.stat().st_mtime >= since]

    if not files:
        raise FileNotFoundError(f"No file found in {folder} matching pattern: {pattern}")

    return max(files, key=lambda p: p.stat().st_mtime)


def list_new_files(folder: Path, pattern: str, since: float) -> List[Path]:
    if not folder.exists():
        return []
    return sorted(
        [f for f in folder.glob(pattern) if f.stat().st_mtime >= since],
        key=lambda p: p.stat().st_mtime,
    )


def quote_path(path: Path) -> str:
    return str(path)


def get_stage_code(stage_name: str) -> str:
    match = re.search(r"Stage\s+(\d+)", stage_name or "")
    return match.group(1) if match else ""


def run_command(cmd: List[str], stage_name: str, dry_run: bool = False, env: Optional[dict] = None) -> None:
    printable = " ".join(shlex.quote(str(part)) for part in cmd)
    logging.info("Running %s", stage_name)
    logging.info("Command: %s", printable)

    if dry_run:
        print(f"[DRY RUN] {printable}")
        return

    completed = subprocess.run(cmd, text=True, env=env)

    if completed.returncode != 0:
        stage_code = get_stage_code(stage_name)
        message = f"{stage_name} failed with return code {completed.returncode}"
        raise PipelineStageError(stage_code=stage_code, stage_name=stage_name, message=message)

    logging.info("%s completed successfully.", stage_name)


def python_cmd() -> str:
    return sys.executable or "python"


def build_app_input_args(args: argparse.Namespace) -> List[str]:
    if args.item_id:
        return ["--item-id", args.item_id]
    return ["--app-url", args.app_url]


def prepare_credentials(args: argparse.Namespace) -> dict:
    """
    Prompts for username/password once for the full pipeline.

    Password is stored only in the child-process environment for this run using
    LHI_ARCGIS_PASSWORD. It is not written to disk and is not placed on the
    command line.
    """
    env = os.environ.copy()

    if args.anonymous:
        return env

    if not args.username:
        args.username = input("ArcGIS username: ").strip()

    if args.username and not env.get("LHI_ARCGIS_PASSWORD"):
        password = getpass.getpass(f"Password for {args.username}: ")
        env["LHI_ARCGIS_PASSWORD"] = password

    return env


# -----------------------------------------------------------------------------
# Pipeline stages
# -----------------------------------------------------------------------------

def run_stage_01(args: argparse.Namespace, outputs: PipelineOutputs, dry_run: bool, env: Optional[dict] = None) -> None:
    start = dt.now().timestamp()

    cmd = [python_cmd(), SCRIPT_01]
    cmd.extend(build_app_input_args(args))

    if args.portal:
        cmd.extend(["--portal", args.portal])
    if args.username and not args.anonymous:
        cmd.extend(["--username", args.username])
    if args.anonymous:
        cmd.append("--anonymous")

    run_command(cmd, "Stage 01 - App Metadata Scan", dry_run=dry_run, env=env)

    if dry_run:
        return

    outputs.app_summary_csv = latest_file(CSV_DIR, "app_summary_*.csv", since=start)
    outputs.raw_exb_json = latest_file(RAW_JSON_DIR, "*.json", since=start)

    logging.info("Stage 01 app summary: %s", outputs.app_summary_csv)
    logging.info("Stage 01 raw ExB JSON: %s", outputs.raw_exb_json)


def run_stage_02(args: argparse.Namespace, outputs: PipelineOutputs, dry_run: bool, env: Optional[dict] = None) -> None:
    if not outputs.raw_exb_json:
        raise RuntimeError("Stage 02 requires raw ExB JSON from Stage 01.")

    start = dt.now().timestamp()

    cmd = [
        python_cmd(),
        SCRIPT_02,
        "--input-json",
        quote_path(outputs.raw_exb_json),
    ]

    if args.output_prefix:
        cmd.extend(["--output-prefix", args.output_prefix])

    run_command(cmd, "Stage 02 - ExB Dependency Extraction", dry_run=dry_run, env=env)

    if dry_run:
        return

    outputs.exb_extraction_summary_csv = latest_file(CSV_DIR, "exb_extraction_summary_*.csv", since=start)
    outputs.widget_inventory_csv = latest_file(CSV_DIR, "widget_inventory_*.csv", since=start)
    outputs.data_source_inventory_csv = latest_file(CSV_DIR, "data_source_inventory_*.csv", since=start)
    outputs.widget_dependencies_csv = latest_file(CSV_DIR, "widget_data_dependencies_*.csv", since=start)
    outputs.webmap_references_csv = latest_file(CSV_DIR, "webmap_references_*.csv", since=start)

    # This file may not be produced if there are no rows, so it is optional.
    layout_files = list_new_files(CSV_DIR, "layout_issues_*.csv", since=start)
    outputs.layout_issues_csv = layout_files[-1] if layout_files else None

    logging.info("Stage 02 webmap references: %s", outputs.webmap_references_csv)
    logging.info("Stage 02 widget dependencies: %s", outputs.widget_dependencies_csv)


def run_stage_03(args: argparse.Namespace, outputs: PipelineOutputs, dry_run: bool, env: Optional[dict] = None) -> None:
    if not outputs.webmap_references_csv or not outputs.widget_dependencies_csv:
        raise RuntimeError("Stage 03 requires webmap references and widget dependencies from Stage 02.")

    start = dt.now().timestamp()

    cmd = [
        python_cmd(),
        SCRIPT_03,
        "--webmap-references-csv",
        quote_path(outputs.webmap_references_csv),
        "--widget-dependencies-csv",
        quote_path(outputs.widget_dependencies_csv),
    ]

    if args.portal:
        cmd.extend(["--portal", args.portal])
    if args.username and not args.anonymous:
        cmd.extend(["--username", args.username])
    if args.anonymous:
        cmd.append("--anonymous")
    if args.output_prefix:
        cmd.extend(["--output-prefix", args.output_prefix])

    run_command(cmd, "Stage 03 - Web Map Layer Scan", dry_run=dry_run, env=env)

    if dry_run:
        return

    outputs.webmap_summary_csv = latest_file(CSV_DIR, "webmap_summary_*.csv", since=start)
    outputs.webmap_layers_csv = latest_file(CSV_DIR, "webmap_layers_*.csv", since=start)
    outputs.exb_resolution_csv = latest_file(CSV_DIR, "exb_layer_reference_resolution_*.csv", since=start)

    table_files = list_new_files(CSV_DIR, "webmap_tables_*.csv", since=start)
    outputs.webmap_tables_csv = table_files[-1] if table_files else None

    logging.info("Stage 03 webmap layers: %s", outputs.webmap_layers_csv)
    logging.info("Stage 03 ExB resolution: %s", outputs.exb_resolution_csv)


def run_stage_04(args: argparse.Namespace, outputs: PipelineOutputs, dry_run: bool, env: Optional[dict] = None) -> None:
    if not outputs.webmap_layers_csv:
        raise RuntimeError("Stage 04 requires webmap layers from Stage 03.")

    start = dt.now().timestamp()

    cmd = [
        python_cmd(),
        SCRIPT_04,
        "--webmap-layers-csv",
        quote_path(outputs.webmap_layers_csv),
        "--timeout",
        str(args.timeout),
    ]

    if args.portal:
        cmd.extend(["--portal", args.portal])

    # Recommended default:
    # - Stage 04 uses anonymous-first health checks.
    # - Do not pass username/password unless auth-first is explicitly requested.
    # - This prevents unnecessary password prompts and avoids false invalid-token behavior on public/non-federated services.
    if args.anonymous or not args.auth_first:
        cmd.append("--anonymous")
    elif args.username:
        cmd.extend(["--username", args.username])

    if args.auth_first:
        cmd.append("--auth-first")
    if args.ignore_definition_expression:
        cmd.append("--ignore-definition-expression")
    if args.output_prefix:
        cmd.extend(["--output-prefix", args.output_prefix])

    run_command(cmd, "Stage 04 - Layer Health Check", dry_run=dry_run, env=env)

    if dry_run:
        return

    outputs.layer_health_summary_csv = latest_file(CSV_DIR, "layer_health_summary_*.csv", since=start)
    outputs.layer_health_details_csv = latest_file(CSV_DIR, "layer_health_details_*.csv", since=start)

    logging.info("Stage 04 layer health summary: %s", outputs.layer_health_summary_csv)
    logging.info("Stage 04 layer health details: %s", outputs.layer_health_details_csv)


def run_stage_05(args: argparse.Namespace, outputs: PipelineOutputs, dry_run: bool, env: Optional[dict] = None) -> None:
    required = {
        "app summary": outputs.app_summary_csv,
        "webmap summary": outputs.webmap_summary_csv,
        "webmap layers": outputs.webmap_layers_csv,
        "ExB resolution": outputs.exb_resolution_csv,
        "layer health details": outputs.layer_health_details_csv,
    }

    for label, path in required.items():
        if not path:
            raise RuntimeError(f"Stage 05 requires {label} from earlier stages.")

    start = dt.now().timestamp()

    cmd = [
        python_cmd(),
        SCRIPT_05,
        "--app-summary-csv",
        quote_path(outputs.app_summary_csv),
        "--webmap-summary-csv",
        quote_path(outputs.webmap_summary_csv),
        "--webmap-layers-csv",
        quote_path(outputs.webmap_layers_csv),
        "--exb-resolution-csv",
        quote_path(outputs.exb_resolution_csv),
        "--layer-health-details-csv",
        quote_path(outputs.layer_health_details_csv),
    ]

    if args.output_prefix:
        cmd.extend(["--output-prefix", args.output_prefix])

    run_command(cmd, "Stage 05 - Sharing Compatibility Check", dry_run=dry_run, env=env)

    if dry_run:
        return

    outputs.sharing_summary_csv = latest_file(CSV_DIR, "sharing_compatibility_summary_*.csv", since=start)
    outputs.sharing_details_csv = latest_file(CSV_DIR, "sharing_compatibility_details_*.csv", since=start)
    outputs.sharing_recommendations_csv = latest_file(CSV_DIR, "sharing_compatibility_recommendations_*.csv", since=start)

    html_files = list_new_files(REPORTS_DIR, "sharing_compatibility_report_*.html", since=start)
    outputs.sharing_html_report = html_files[-1] if html_files else None

    logging.info("Stage 05 sharing summary: %s", outputs.sharing_summary_csv)
    logging.info("Stage 05 HTML report: %s", outputs.sharing_html_report)


# -----------------------------------------------------------------------------
# CLI
# -----------------------------------------------------------------------------

def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run the full LHI ExB App Inspector pipeline for one Experience Builder app."
    )

    app_input = parser.add_mutually_exclusive_group(required=True)
    app_input.add_argument("--item-id", help="32-character Experience Builder app item ID.")
    app_input.add_argument("--app-url", help="Experience Builder app URL or ArcGIS item URL.")

    parser.add_argument(
        "--portal",
        default="https://www.arcgis.com",
        help="Portal URL. Example: https://www.arcgis.com or https://yourorg.maps.arcgis.com",
    )
    parser.add_argument(
        "--username",
        default=None,
        help="Portal username. If omitted and --anonymous is not used, the script will ask once.",
    )
    parser.add_argument(
        "--anonymous",
        action="store_true",
        help="Run using anonymous access where possible.",
    )
    parser.add_argument(
        "--timeout",
        type=int,
        default=30,
        help="REST request timeout in seconds for Script 04. Default: 30.",
    )
    parser.add_argument(
        "--auth-first",
        action="store_true",
        help="Pass --auth-first to Script 04. Default remains anonymous-first.",
    )
    parser.add_argument(
        "--ignore-definition-expression",
        action="store_true",
        help="Pass --ignore-definition-expression to Script 04.",
    )
    parser.add_argument(
        "--output-prefix",
        default=None,
        help="Optional output prefix passed to Scripts 02-05.",
    )
    parser.add_argument(
        "--skip-stage",
        action="append",
        choices=["01", "02", "03", "04", "05"],
        default=[],
        help="Skip a stage. Mostly for debugging; not recommended for normal runs.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print commands without running them.",
    )

    return parser.parse_args()


def print_final_summary(outputs: PipelineOutputs, log_path: Path) -> None:
    print("\n=== LHI ExB App Inspector: Full Single-App Inspection Complete ===")
    print("\nKey outputs:")

    if outputs.app_summary_csv:
        print(f"App summary: {outputs.app_summary_csv}")
    if outputs.raw_exb_json:
        print(f"Raw ExB JSON: {outputs.raw_exb_json}")
    if outputs.webmap_references_csv:
        print(f"Web map references: {outputs.webmap_references_csv}")
    if outputs.webmap_layers_csv:
        print(f"Web map layers: {outputs.webmap_layers_csv}")
    if outputs.exb_resolution_csv:
        print(f"ExB layer resolution: {outputs.exb_resolution_csv}")
    if outputs.layer_health_summary_csv:
        print(f"Layer health summary: {outputs.layer_health_summary_csv}")
    if outputs.layer_health_details_csv:
        print(f"Layer health details: {outputs.layer_health_details_csv}")
    if outputs.sharing_summary_csv:
        print(f"Sharing summary: {outputs.sharing_summary_csv}")
    if outputs.sharing_recommendations_csv:
        print(f"Sharing recommendations: {outputs.sharing_recommendations_csv}")
    if outputs.sharing_html_report:
        print(f"HTML report: {outputs.sharing_html_report}")

    print(f"\nPipeline log: {log_path}")

    if outputs.sharing_html_report:
        print("\nOpen the HTML report in your browser for the easiest review.")


def main() -> int:
    log_path = setup_logging()
    args = parse_args()
    outputs = PipelineOutputs()

    try:
        for script in [SCRIPT_01, SCRIPT_02, SCRIPT_03, SCRIPT_04, SCRIPT_05]:
            require_file(Path(script), f"Required script {script}")

        logging.info("Starting full ExB app inspection pipeline.")

        env = prepare_credentials(args)

        if "01" not in args.skip_stage:
            run_stage_01(args, outputs, args.dry_run, env=env)

        if "02" not in args.skip_stage:
            run_stage_02(args, outputs, args.dry_run, env=env)

        if "03" not in args.skip_stage:
            run_stage_03(args, outputs, args.dry_run, env=env)

        if "04" not in args.skip_stage:
            run_stage_04(args, outputs, args.dry_run, env=env)

        if "05" not in args.skip_stage:
            run_stage_05(args, outputs, args.dry_run, env=env)

        print_final_summary(outputs, log_path)
        return 0

    except PipelineStageError as exc:
        logging.exception("Full inspection pipeline failed at %s: %s", exc.stage_name, exc.message)
        print("\nFull inspection pipeline failed. Check the log file for details:")
        print(log_path)
        print(f"Failed stage: {exc.stage_name}")
        print(f"Error: {exc.message}")
        print(f"LHI_FAILED_STAGE_CODE={exc.stage_code}")
        print(f"LHI_FAILED_STAGE_NAME={exc.stage_name}")
        print(f"LHI_ERROR_MESSAGE={exc.message}")
        return 1

    except Exception as exc:
        logging.exception("Full inspection pipeline failed: %s", exc)
        print("\nFull inspection pipeline failed. Check the log file for details:")
        print(log_path)
        print(f"Failed stage: unknown")
        print(f"Error: {exc}")
        print("LHI_FAILED_STAGE_CODE=")
        print("LHI_FAILED_STAGE_NAME=unknown")
        print(f"LHI_ERROR_MESSAGE={exc}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
