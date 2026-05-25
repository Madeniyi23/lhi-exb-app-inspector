"""
LHI ExB App Inspector
Script 07: Multi-App Inspection Runner v0.9

Purpose:
- Read a CSV list of Experience Builder apps
- Run Script 06 for each app
- Prompt for username/password once
- Reuse the password safely through the LHI_ARCGIS_PASSWORD environment variable
- Produce a master CSV and richer self-contained master HTML report across all scanned apps
- Capture failed stage and error message from Script 06 child runs
- Package each batch into outputs/batches/<batch_id> for easier sharing and archiving

Required input CSV columns:
- app_item_id OR app_url
Optional columns:
- app_name
- notes

Example input_apps.csv:
app_item_id,app_name,notes
7bd1b4f0533244a994c7f8c5a1bcc1db,Our Community Partners,Test app
xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx,Another ExB App,Another test

Author: Lazy Hat Innovations
"""

from __future__ import annotations

import argparse
import csv
import getpass
import html
import logging
import os
import re
import shutil
import shlex
import subprocess
import sys
from dataclasses import dataclass, asdict, replace
from datetime import datetime as dt, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional


# -----------------------------------------------------------------------------
# Defaults
# -----------------------------------------------------------------------------

OUTPUT_ROOT = Path("outputs")
CSV_DIR = OUTPUT_ROOT / "csv"
LOG_DIR = OUTPUT_ROOT / "logs"
REPORTS_DIR = OUTPUT_ROOT / "reports"
MULTI_APP_DIR = OUTPUT_ROOT / "multi_app"
BATCHES_DIR = OUTPUT_ROOT / "batches"

SCRIPT_06 = "06_run_full_exb_inspection.py"


# -----------------------------------------------------------------------------
# Data models
# -----------------------------------------------------------------------------

@dataclass
class MultiAppMasterRow:
    scan_timestamp_utc: str
    batch_id: str
    app_input_name: str
    app_item_id: str
    app_url: str
    app_title: str
    app_access: str
    webmap_count: str
    webmap_access_summary: str
    layer_count: str
    public_reachable_layer_count: str
    authenticated_layer_count: str
    inaccessible_layer_count: str
    active_dependency_count: str
    active_dependency_layer_count: str
    template_residue_dependency_count: str
    possible_broken_dependency_count: str
    overall_status: str
    overall_risk_level: str
    overall_risk_score: str
    scan_status: str
    failed_stage_code: str
    failed_stage_name: str
    error_message: str
    sharing_summary_csv: str
    sharing_recommendations_csv: str
    html_report_path: str
    log_file: str
    notes: str


# -----------------------------------------------------------------------------
# Setup/utilities
# -----------------------------------------------------------------------------

def setup_output_dirs() -> None:
    for folder in [OUTPUT_ROOT, CSV_DIR, LOG_DIR, REPORTS_DIR, MULTI_APP_DIR, BATCHES_DIR]:
        folder.mkdir(parents=True, exist_ok=True)


def now_utc_string() -> str:
    return dt.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")


def setup_logging(batch_id: str) -> Path:
    setup_output_dirs()
    log_path = LOG_DIR / f"run_multi_exb_inspection_{batch_id}.log"

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s | %(levelname)s | %(message)s",
        handlers=[
            logging.FileHandler(log_path, encoding="utf-8"),
            logging.StreamHandler(sys.stdout),
        ],
    )
    return log_path


def sanitize_value(value: str, max_len: int = 80) -> str:
    value = (value or "").strip()
    value = re.sub(r"[^A-Za-z0-9_\-]+", "_", value)
    value = re.sub(r"_+", "_", value).strip("_")
    return value[:max_len] if value else "unnamed_app"


def safe_str(value: Any) -> str:
    if value is None:
        return ""
    return str(value)


def read_csv_dicts(path: Path) -> List[Dict[str, str]]:
    with path.open("r", newline="", encoding="utf-8-sig") as f:
        return list(csv.DictReader(f))


def write_csv(path: Path, rows: List[Any]) -> None:
    if not rows:
        logging.warning("No rows to write for: %s", path)
        return

    with path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=list(asdict(rows[0]).keys()))
        writer.writeheader()
        for row in rows:
            writer.writerow(asdict(row))

    logging.info("CSV written: %s | rows: %s", path, len(rows))


def latest_file(folder: Path, pattern: str, since: Optional[float] = None) -> Optional[Path]:
    if not folder.exists():
        return None

    files = list(folder.glob(pattern))
    if since is not None:
        files = [f for f in files if f.stat().st_mtime >= since]

    if not files:
        return None

    return max(files, key=lambda p: p.stat().st_mtime)


def list_new_files(folder: Path, pattern: str, since: float) -> List[Path]:
    if not folder.exists():
        return []
    return sorted(
        [f for f in folder.glob(pattern) if f.stat().st_mtime >= since],
        key=lambda p: p.stat().st_mtime,
    )


def relative_or_string(path: Optional[Path]) -> str:
    if not path:
        return ""
    try:
        return str(path.relative_to(Path.cwd()))
    except Exception:
        return str(path)


def read_first_csv_row(path: Optional[Path]) -> Dict[str, str]:
    if not path or not path.exists():
        return {}
    rows = read_csv_dicts(path)
    return rows[0] if rows else {}


def python_cmd() -> str:
    return sys.executable or "python"


def build_child_env(args: argparse.Namespace) -> Dict[str, str]:
    env = os.environ.copy()

    if args.anonymous:
        return env

    if not args.username:
        args.username = input("ArcGIS username: ").strip()

    if args.username and not env.get("LHI_ARCGIS_PASSWORD"):
        password = getpass.getpass(f"Password for {args.username}: ")
        env["LHI_ARCGIS_PASSWORD"] = password

    return env


def validate_apps(rows: List[Dict[str, str]]) -> None:
    if not rows:
        raise RuntimeError("Apps CSV is empty.")

    has_valid = False
    for row in rows:
        if (row.get("app_item_id") or row.get("item_id") or row.get("app_url") or "").strip():
            has_valid = True
            break

    if not has_valid:
        raise RuntimeError("Apps CSV must contain at least one app_item_id, item_id, or app_url value.")


def get_app_identifier(row: Dict[str, str]) -> Dict[str, str]:
    item_id = (row.get("app_item_id") or row.get("item_id") or "").strip()
    app_url = (row.get("app_url") or "").strip()
    app_name = (row.get("app_name") or row.get("name") or row.get("title") or "").strip()
    notes = (row.get("notes") or "").strip()

    if not item_id and not app_url:
        raise ValueError("Row has no app_item_id/item_id or app_url.")

    return {
        "item_id": item_id,
        "app_url": app_url,
        "app_name": app_name or item_id or app_url,
        "notes": notes,
    }


def extract_failure_markers(output_text: str) -> Dict[str, str]:
    markers = {
        "failed_stage_code": "",
        "failed_stage_name": "",
        "error_message": "",
    }

    for line in (output_text or "").splitlines():
        line = line.strip()
        if line.startswith("LHI_FAILED_STAGE_CODE="):
            markers["failed_stage_code"] = line.split("=", 1)[1].strip()
        elif line.startswith("LHI_FAILED_STAGE_NAME="):
            markers["failed_stage_name"] = line.split("=", 1)[1].strip()
        elif line.startswith("LHI_ERROR_MESSAGE="):
            markers["error_message"] = line.split("=", 1)[1].strip()

    # Fallback parsing for older Script 06 versions.
    if not markers["error_message"]:
        for line in reversed((output_text or "").splitlines()):
            line = line.strip()
            if line.startswith("Error:"):
                markers["error_message"] = line.replace("Error:", "", 1).strip()
                break

    if not markers["failed_stage_name"]:
        match = re.search(r"(Stage\s+\d+\s+-\s+[^\\r\\n]+?)\s+failed", output_text or "", flags=re.IGNORECASE)
        if match:
            markers["failed_stage_name"] = match.group(1).strip()
            code_match = re.search(r"Stage\s+(\d+)", markers["failed_stage_name"])
            if code_match and not markers["failed_stage_code"]:
                markers["failed_stage_code"] = code_match.group(1)

    return markers


def run_script06_for_app(
    app_info: Dict[str, str],
    args: argparse.Namespace,
    env: Dict[str, str],
    batch_id: str,
) -> Dict[str, Any]:
    start = dt.now().timestamp()

    app_name = app_info["app_name"]
    item_id = app_info["item_id"]
    app_url = app_info["app_url"]

    prefix_base = sanitize_value(app_name or item_id or "exb_app")
    short_id = item_id[:8] if item_id else "url"
    output_prefix = f"{batch_id}_{prefix_base}_{short_id}"

    cmd = [
        python_cmd(),
        SCRIPT_06,
        "--portal",
        args.portal,
        "--output-prefix",
        output_prefix,
        "--timeout",
        str(args.timeout),
    ]

    if item_id:
        cmd.extend(["--item-id", item_id])
    else:
        cmd.extend(["--app-url", app_url])

    if args.anonymous:
        cmd.append("--anonymous")
    elif args.username:
        cmd.extend(["--username", args.username])

    if args.auth_first:
        cmd.append("--auth-first")

    if args.ignore_definition_expression:
        cmd.append("--ignore-definition-expression")

    logging.info("Scanning app: %s", app_name)
    logging.info("Command: %s", " ".join(shlex.quote(str(part)) for part in cmd))

    if args.dry_run:
        print("[DRY RUN]", " ".join(shlex.quote(str(part)) for part in cmd))
        return {
            "returncode": 0,
            "start": start,
            "error": "",
            "failed_stage_code": "",
            "failed_stage_name": "",
            "stdout": "",
            "stderr": "",
            "skipped": True,
        }

    completed = subprocess.run(
        cmd,
        text=True,
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )

    # Re-print child output so the user still sees progress in the console.
    if completed.stdout:
        print(completed.stdout, end="")
    if completed.stderr:
        print(completed.stderr, end="", file=sys.stderr)

    combined_output = "\\n".join([completed.stdout or "", completed.stderr or ""])
    markers = extract_failure_markers(combined_output)

    error = ""
    if completed.returncode != 0:
        error = markers.get("error_message") or f"Script 06 failed with return code {completed.returncode}"

    return {
        "returncode": completed.returncode,
        "start": start,
        "error": error,
        "failed_stage_code": markers.get("failed_stage_code", ""),
        "failed_stage_name": markers.get("failed_stage_name", ""),
        "stdout": completed.stdout or "",
        "stderr": completed.stderr or "",
        "skipped": False,
    }


def build_master_row_from_outputs(
    app_info: Dict[str, str],
    batch_id: str,
    scan_result: Dict[str, Any],
) -> MultiAppMasterRow:
    start = scan_result["start"]

    sharing_summary = latest_file(CSV_DIR, "sharing_compatibility_summary_*.csv", since=start)
    sharing_recs = latest_file(CSV_DIR, "sharing_compatibility_recommendations_*.csv", since=start)
    html_report = latest_file(REPORTS_DIR, "sharing_compatibility_report_*.html", since=start)
    pipeline_log = latest_file(LOG_DIR, "run_full_exb_inspection_*.log", since=start)

    summary_row = read_first_csv_row(sharing_summary)

    scan_ok = scan_result["returncode"] == 0 and bool(summary_row)
    scan_status = "success" if scan_ok else "failed"

    return MultiAppMasterRow(
        scan_timestamp_utc=now_utc_string(),
        batch_id=batch_id,
        app_input_name=app_info.get("app_name", ""),
        app_item_id=summary_row.get("app_item_id", app_info.get("item_id", "")),
        app_url=app_info.get("app_url", ""),
        app_title=summary_row.get("app_title", ""),
        app_access=summary_row.get("app_access", ""),
        webmap_count=summary_row.get("webmap_count", ""),
        webmap_access_summary=summary_row.get("webmap_access_summary", ""),
        layer_count=summary_row.get("layer_count", ""),
        public_reachable_layer_count=summary_row.get("public_reachable_layer_count", ""),
        authenticated_layer_count=summary_row.get("authenticated_layer_count", ""),
        inaccessible_layer_count=summary_row.get("inaccessible_layer_count", ""),
        active_dependency_count=summary_row.get("active_dependency_count", ""),
        active_dependency_layer_count=summary_row.get("active_dependency_layer_count", ""),
        template_residue_dependency_count=summary_row.get("template_residue_dependency_count", ""),
        possible_broken_dependency_count=summary_row.get("possible_broken_dependency_count", ""),
        overall_status=summary_row.get("overall_status", ""),
        overall_risk_level=summary_row.get("overall_risk_level", ""),
        overall_risk_score=summary_row.get("overall_risk_score", ""),
        scan_status=scan_status,
        failed_stage_code=scan_result.get("failed_stage_code", ""),
        failed_stage_name=scan_result.get("failed_stage_name", ""),
        error_message=scan_result.get("error", ""),
        sharing_summary_csv=relative_or_string(sharing_summary),
        sharing_recommendations_csv=relative_or_string(sharing_recs),
        html_report_path=relative_or_string(html_report),
        log_file=relative_or_string(pipeline_log),
        notes=app_info.get("notes", ""),
    )


def status_class(value: str) -> str:
    v = (value or "").lower()
    if v in {"ok", "success", "low"}:
        return "ok"
    if v in {"review", "medium"}:
        return "review"
    if v in {"warning", "high"}:
        return "warning"
    if v in {"fail", "failed", "critical"}:
        return "critical"
    return "neutral"


def generate_master_html(rows: List[MultiAppMasterRow], output_path: Path, batch_id: str) -> None:
    """
    Generates a richer, self-contained multi-app master report.

    v0.8 improvements:
    - Dashboard cards for portfolio-level review
    - Search/filter controls
    - One row per app
    - Expandable detail row per app
    - Per-app quick diagnosis
    - Per-app recommendation/next action
    - Fixed relative links to individual HTML reports
    """
    total = len(rows)
    success = sum(1 for r in rows if r.scan_status == "success")
    failed = sum(1 for r in rows if r.scan_status != "success")
    critical = sum(1 for r in rows if (r.overall_risk_level or "").lower() == "critical")
    high = sum(1 for r in rows if (r.overall_risk_level or "").lower() == "high")
    medium = sum(1 for r in rows if (r.overall_risk_level or "").lower() == "medium")
    low = sum(1 for r in rows if (r.overall_risk_level or "").lower() == "low")

    def to_int(value: Any) -> int:
        try:
            if value in [None, ""]:
                return 0
            return int(float(value))
        except Exception:
            return 0

    total_layers = sum(to_int(r.layer_count) for r in rows)
    total_public_layers = sum(to_int(r.public_reachable_layer_count) for r in rows)
    total_inaccessible = sum(to_int(r.inaccessible_layer_count) for r in rows)
    total_broken = sum(to_int(r.possible_broken_dependency_count) for r in rows)
    total_template_residue = sum(to_int(r.template_residue_dependency_count) for r in rows)
    total_active_deps = sum(to_int(r.active_dependency_count) for r in rows)

    def esc(value: Any) -> str:
        return html.escape(safe_str(value))

    def app_action(row: MultiAppMasterRow) -> str:
        if row.scan_status != "success":
            stage = row.failed_stage_name or "unknown stage"
            return f"Review scan failure at {stage}. Use the captured error message and child log to fix the input, access, service, or script issue before rerunning."
        risk = (row.overall_risk_level or "").lower()
        broken = to_int(row.possible_broken_dependency_count)
        inaccessible = to_int(row.inaccessible_layer_count)
        residue = to_int(row.template_residue_dependency_count)

        if risk in {"critical", "high"} or broken or inaccessible:
            return "Prioritize technical review. Check inaccessible layers, broken dependencies, sharing, and widget configuration."
        if risk == "medium":
            return "Review before publishing or migrating. Confirm warnings are expected and not user-facing."
        if residue:
            return "Operationally OK. Treat template residue as a maintenance note unless visible widgets are failing."
        return "No immediate action required."

    def app_diagnosis(row: MultiAppMasterRow) -> str:
        if row.scan_status != "success":
            stage = row.failed_stage_name or "unknown stage"
            return f"Scan failed at {stage}: {row.error_message or 'No details captured.'}"
        return (
            f"App sharing is '{row.app_access or 'unknown'}'. "
            f"Web map sharing is '{row.webmap_access_summary or 'unknown'}'. "
            f"{row.public_reachable_layer_count or 0} of {row.layer_count or 0} layer(s) were publicly reachable. "
            f"{row.possible_broken_dependency_count or 0} possible broken active dependency/dependencies. "
            f"{row.template_residue_dependency_count or 0} likely template/copy residue reference(s)."
        )

    row_html = []
    for idx, row in enumerate(rows, start=1):
        report_link = ""
        if row.html_report_path:
            report_path = Path(row.html_report_path)
            try:
                href_path = Path(os.path.relpath(report_path, start=output_path.parent))
            except Exception:
                href_path = report_path
            href = str(href_path).replace("\\", "/")
            report_link = f'<a class="link-button" href="{esc(href)}" target="_blank">Open individual report</a>'
        else:
            report_link = '<span class="muted">No report</span>'

        detail_id = f"detail_{idx}"
        risk_class = status_class(row.overall_risk_level)
        scan_class = status_class(row.scan_status)
        action = app_action(row)
        diagnosis = app_diagnosis(row)

        row_html.append(f"""
        <tr class="app-row" data-risk="{esc((row.overall_risk_level or '').lower())}" data-status="{esc((row.scan_status or '').lower())}" data-access="{esc((row.app_access or '').lower())}">
          <td>
            <button class="toggle" onclick="toggleDetails('{detail_id}', this)">+</button>
            <strong>{esc(row.app_title or row.app_input_name or 'Untitled app')}</strong>
            <div class="subtle">{esc(row.app_input_name)}</div>
          </td>
          <td class="mono">{esc(row.app_item_id)}</td>
          <td><span class="badge {scan_class}">{esc(row.scan_status)}</span><div class="subtle">{esc(row.failed_stage_name)}</div></td>
          <td><span class="badge {risk_class}">{esc(row.overall_risk_level or 'unknown')}</span></td>
          <td>{esc(row.overall_status)}</td>
          <td>{esc(row.app_access)}</td>
          <td>{esc(row.webmap_access_summary)}</td>
          <td>{esc(row.layer_count)}</td>
          <td>{esc(row.public_reachable_layer_count)}</td>
          <td>{esc(row.inaccessible_layer_count)}</td>
          <td>{esc(row.possible_broken_dependency_count)}</td>
          <td>{esc(row.template_residue_dependency_count)}</td>
          <td>{report_link}</td>
        </tr>
        <tr id="{detail_id}" class="detail-row" style="display:none;">
          <td colspan="13">
            <div class="detail-panel">
              <div class="detail-grid">
                <div>
                  <h4>Diagnosis</h4>
                  <p>{esc(diagnosis)}</p>
                </div>
                <div>
                  <h4>Recommended next action</h4>
                  <p>{esc(action)}</p>
                </div>
                <div>
                  <h4>Dependency summary</h4>
                  <p><strong>Active dependencies:</strong> {esc(row.active_dependency_count)} across {esc(row.active_dependency_layer_count)} layer(s)</p>
                  <p><strong>Template residue:</strong> {esc(row.template_residue_dependency_count)}</p>
                  <p><strong>Possible broken dependencies:</strong> {esc(row.possible_broken_dependency_count)}</p>
                </div>
                <div>
                  <h4>Output links</h4>
                  <p>{report_link}</p>
                  <p class="small"><strong>Summary CSV:</strong> {esc(row.sharing_summary_csv)}</p>
                  <p class="small"><strong>Log:</strong> {esc(row.log_file)}</p>
                </div>
              </div>
              <div class="notes">
                <strong>Failed stage:</strong> {esc(row.failed_stage_name or 'None')}<br>
                <strong>Failed stage code:</strong> {esc(row.failed_stage_code or 'None')}<br>
                <strong>Notes/Error:</strong> {esc(row.error_message or row.notes or 'None')}
              </div>
            </div>
          </td>
        </tr>
        """)

    inaccessible_card_class = "critical" if total_inaccessible else "ok"
    broken_card_class = "critical" if total_broken else "ok"

    html_text = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>LHI ExB App Inspector - Multi-App Master Report v0.8</title>
<meta name="viewport" content="width=device-width, initial-scale=1">
<style>
:root {{
  --bg: #f7f8fb;
  --panel: #ffffff;
  --text: #1f2937;
  --muted: #6b7280;
  --border: #e5e7eb;
  --ok: #047857;
  --ok-bg: #d1fae5;
  --review: #92400e;
  --review-bg: #fef3c7;
  --warning: #b45309;
  --warning-bg: #ffedd5;
  --critical: #b91c1c;
  --critical-bg: #fee2e2;
  --neutral-bg: #f3f4f6;
}}
* {{ box-sizing: border-box; }}
body {{
  margin: 0;
  font-family: Segoe UI, Arial, sans-serif;
  background: var(--bg);
  color: var(--text);
}}
header {{
  background: linear-gradient(135deg, #111827, #374151);
  color: white;
  padding: 28px 34px;
}}
header h1 {{ margin: 0 0 8px 0; font-size: 28px; }}
header p {{ margin: 0; color: #d1d5db; }}
main {{ padding: 24px 34px 44px; }}
.section {{
  background: var(--panel);
  border: 1px solid var(--border);
  border-radius: 16px;
  padding: 20px;
  margin-bottom: 20px;
  box-shadow: 0 6px 18px rgba(0,0,0,0.04);
}}
.grid {{
  display: grid;
  grid-template-columns: repeat(6, minmax(140px, 1fr));
  gap: 14px;
}}
.card {{
  border-radius: 14px;
  padding: 16px;
  border: 1px solid var(--border);
  background: var(--neutral-bg);
}}
.card.ok {{ background: var(--ok-bg); }}
.card.review {{ background: var(--review-bg); }}
.card.warning {{ background: var(--warning-bg); }}
.card.critical {{ background: var(--critical-bg); }}
.card-title {{
  font-size: 12px;
  text-transform: uppercase;
  color: var(--muted);
  letter-spacing: .04em;
}}
.card-value {{
  font-size: 24px;
  font-weight: 700;
  margin-top: 4px;
}}
.card-subtitle {{
  font-size: 12px;
  color: var(--muted);
  margin-top: 4px;
}}
.badge {{
  display: inline-block;
  padding: 4px 9px;
  border-radius: 999px;
  font-size: 12px;
  font-weight: 700;
  white-space: nowrap;
}}
.badge.ok {{ background: var(--ok-bg); color: var(--ok); }}
.badge.review {{ background: var(--review-bg); color: var(--review); }}
.badge.warning {{ background: var(--warning-bg); color: var(--warning); }}
.badge.critical {{ background: var(--critical-bg); color: var(--critical); }}
.badge.neutral {{ background: var(--neutral-bg); color: var(--text); }}
.controls {{
  display: flex;
  gap: 10px;
  margin-bottom: 14px;
  flex-wrap: wrap;
}}
input, select {{
  padding: 10px 12px;
  border: 1px solid var(--border);
  border-radius: 10px;
  min-width: 220px;
}}
table {{
  width: 100%;
  border-collapse: collapse;
  font-size: 14px;
}}
th, td {{
  border-bottom: 1px solid var(--border);
  padding: 10px;
  vertical-align: top;
  text-align: left;
}}
th {{
  background: #f9fafb;
  position: sticky;
  top: 0;
  z-index: 1;
}}
.small {{ font-size: 12px; color: #4b5563; max-width: 520px; }}
.subtle {{ font-size: 12px; color: var(--muted); margin-top: 3px; }}
.muted {{ color: var(--muted); }}
.mono {{ font-family: Consolas, monospace; font-size: 12px; }}
.toggle {{
  border: 1px solid var(--border);
  background: white;
  border-radius: 8px;
  padding: 2px 8px;
  margin-right: 8px;
  cursor: pointer;
  font-weight: 700;
}}
.detail-row td {{ background: #fbfdff; }}
.detail-panel {{
  border: 1px solid var(--border);
  border-radius: 14px;
  padding: 16px;
  background: white;
}}
.detail-grid {{
  display: grid;
  grid-template-columns: repeat(4, minmax(220px, 1fr));
  gap: 16px;
}}
.detail-grid h4 {{ margin: 0 0 6px 0; }}
.detail-grid p {{ margin: 0 0 6px 0; line-height: 1.45; }}
.notes {{
  margin-top: 12px;
  padding-top: 12px;
  border-top: 1px solid var(--border);
  color: #374151;
}}
.link-button {{
  display: inline-block;
  padding: 6px 10px;
  border-radius: 9px;
  background: #111827;
  color: white;
  text-decoration: none;
  font-size: 12px;
  font-weight: 700;
}}
.note {{ line-height: 1.55; color: #374151; }}
.footer {{ color: var(--muted); font-size: 12px; margin-top: 18px; }}
@media (max-width: 1400px) {{
  .grid {{ grid-template-columns: repeat(3, 1fr); }}
  .detail-grid {{ grid-template-columns: repeat(2, 1fr); }}
}}
@media (max-width: 760px) {{
  main {{ padding: 16px; }}
  .grid, .detail-grid {{ grid-template-columns: 1fr; }}
  table {{ font-size: 12px; }}
}}
</style>
</head>
<body>
<header>
  <h1>LHI ExB App Inspector</h1>
  <p>Multi-App Master Report v0.8 · Batch {esc(batch_id)} · Generated {esc(now_utc_string())}</p>
</header>

<main>
  <section class="section">
    <h2>Portfolio Summary</h2>
    <div class="grid">
      <div class="card neutral"><div class="card-title">Apps Scanned</div><div class="card-value">{total}</div><div class="card-subtitle">Total submitted</div></div>
      <div class="card ok"><div class="card-title">Successful</div><div class="card-value">{success}</div><div class="card-subtitle">Completed scans</div></div>
      <div class="card critical"><div class="card-title">Failed</div><div class="card-value">{failed}</div><div class="card-subtitle">Need rerun/review</div></div>
      <div class="card ok"><div class="card-title">Low Risk</div><div class="card-value">{low}</div><div class="card-subtitle">Operationally healthy</div></div>
      <div class="card review"><div class="card-title">Medium Risk</div><div class="card-value">{medium}</div><div class="card-subtitle">Review recommended</div></div>
      <div class="card warning"><div class="card-title">High Risk</div><div class="card-value">{high}</div><div class="card-subtitle">Prioritize</div></div>
      <div class="card critical"><div class="card-title">Critical Risk</div><div class="card-value">{critical}</div><div class="card-subtitle">Immediate attention</div></div>
      <div class="card neutral"><div class="card-title">Layers</div><div class="card-value">{total_layers}</div><div class="card-subtitle">{total_public_layers} public reachable</div></div>
      <div class="card {inaccessible_card_class}"><div class="card-title">Inaccessible Layers</div><div class="card-value">{total_inaccessible}</div><div class="card-subtitle">Across all apps</div></div>
      <div class="card {broken_card_class}"><div class="card-title">Broken Dependencies</div><div class="card-value">{total_broken}</div><div class="card-subtitle">Active dependency risk</div></div>
      <div class="card neutral"><div class="card-title">Active Dependencies</div><div class="card-value">{total_active_deps}</div><div class="card-subtitle">Resolved widget dependencies</div></div>
      <div class="card review"><div class="card-title">Template Residue</div><div class="card-value">{total_template_residue}</div><div class="card-subtitle">Maintenance notes</div></div>
    </div>
  </section>

  <section class="section">
    <h2>App Review Table</h2>
    <p class="note">Use this table as the main review surface. Expand each app row for diagnosis, recommended next action, dependencies, and output links.</p>
    <div class="controls">
      <input type="text" id="searchBox" placeholder="Search apps, IDs, status, notes...">
      <select id="riskFilter">
        <option value="">All risks</option>
        <option value="low">Low</option>
        <option value="medium">Medium</option>
        <option value="high">High</option>
        <option value="critical">Critical</option>
      </select>
      <select id="statusFilter">
        <option value="">All statuses</option>
        <option value="success">Success</option>
        <option value="failed">Failed</option>
      </select>
      <select id="accessFilter">
        <option value="">All app access</option>
        <option value="public">Public</option>
        <option value="org">Org</option>
        <option value="private">Private</option>
      </select>
    </div>

    <table id="masterTable">
      <thead>
        <tr>
          <th>App</th>
          <th>Item ID</th>
          <th>Scan</th>
          <th>Risk</th>
          <th>Status</th>
          <th>App Access</th>
          <th>Web Map Access</th>
          <th>Layers</th>
          <th>Public Layers</th>
          <th>Inaccessible</th>
          <th>Broken Deps</th>
          <th>Template Residue</th>
          <th>Report</th>
        </tr>
      </thead>
      <tbody>
        {''.join(row_html)}
      </tbody>
    </table>

    <div class="footer">Lazy Hat Innovations · Build fast. Think deeply. Publish strategically.</div>
  </section>

  <section class="section">
    <h2>How to interpret this report</h2>
    <p class="note"><strong>Operational risk</strong> comes from active dependency, layer access, and sharing issues. <strong>Template residue</strong> is treated as a maintenance note unless related widgets are visible and failing.</p>
    <p class="note">The individual app reports are still generated for deep technical review. This master report is intended for portfolio review and triage.</p>
  </section>
</main>

<script>
function filterTable() {{
  const search = document.getElementById('searchBox').value.toLowerCase();
  const risk = document.getElementById('riskFilter').value.toLowerCase();
  const status = document.getElementById('statusFilter').value.toLowerCase();
  const access = document.getElementById('accessFilter').value.toLowerCase();
  const rows = document.querySelectorAll('#masterTable tbody tr.app-row');

  rows.forEach(row => {{
    const text = row.innerText.toLowerCase();
    const rowRisk = (row.getAttribute('data-risk') || '').toLowerCase();
    const rowStatus = (row.getAttribute('data-status') || '').toLowerCase();
    const rowAccess = (row.getAttribute('data-access') || '').toLowerCase();
    const detail = row.nextElementSibling;

    const matchesSearch = !search || text.includes(search) || (detail && detail.innerText.toLowerCase().includes(search));
    const matchesRisk = !risk || rowRisk === risk;
    const matchesStatus = !status || rowStatus === status;
    const matchesAccess = !access || rowAccess === access;

    const show = matchesSearch && matchesRisk && matchesStatus && matchesAccess;
    row.style.display = show ? '' : 'none';

    if (!show && detail && detail.classList.contains('detail-row')) {{
      detail.style.display = 'none';
      const btn = row.querySelector('.toggle');
      if (btn) btn.textContent = '+';
    }}
  }});
}}

function toggleDetails(id, btn) {{
  const row = document.getElementById(id);
  if (!row) return;
  const isHidden = row.style.display === 'none';
  row.style.display = isHidden ? '' : 'none';
  btn.textContent = isHidden ? '−' : '+';
}}

document.getElementById('searchBox').addEventListener('input', filterTable);
document.getElementById('riskFilter').addEventListener('change', filterTable);
document.getElementById('statusFilter').addEventListener('change', filterTable);
document.getElementById('accessFilter').addEventListener('change', filterTable);
</script>
</body>
</html>
"""
    output_path.write_text(html_text, encoding="utf-8")
    logging.info("Master HTML report written: %s", output_path)


# -----------------------------------------------------------------------------
# Batch packaging
# -----------------------------------------------------------------------------

def resolve_existing_path(path_text: str) -> Optional[Path]:
    if not path_text:
        return None
    path = Path(path_text)
    if path.exists():
        return path
    alt = Path.cwd() / path_text
    if alt.exists():
        return alt
    return None


def copy_file_if_exists(source_text: str, destination: Path) -> str:
    source = resolve_existing_path(source_text)
    if not source:
        return ""

    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source, destination)
    return relative_or_string(destination)


def package_batch_outputs(
    rows: List[MultiAppMasterRow],
    master_csv: Path,
    master_html: Path,
    batch_log: Path,
    batch_id: str,
) -> Dict[str, Path]:
    """
    Creates a clean batch folder while preserving the original output folders.

    Structure:
    outputs/batches/<batch_id>/
      master/
        master_summary.csv
        master_report.html
      apps/
        <app_name_itemid>/
          individual_report.html
          sharing_summary.csv
          sharing_recommendations.csv
          app_scan.log
      logs/
        batch_log.log
    """
    batch_root = BATCHES_DIR / batch_id
    master_dir = batch_root / "master"
    apps_dir = batch_root / "apps"
    logs_dir = batch_root / "logs"

    for folder in [batch_root, master_dir, apps_dir, logs_dir]:
        folder.mkdir(parents=True, exist_ok=True)

    packaged_rows: List[MultiAppMasterRow] = []

    for row in rows:
        app_label = sanitize_value(row.app_title or row.app_input_name or row.app_item_id or "unknown_app")
        short_id = (row.app_item_id or "unknown")[:8]
        app_dir = apps_dir / f"{app_label}_{short_id}"
        app_dir.mkdir(parents=True, exist_ok=True)

        new_html = copy_file_if_exists(row.html_report_path, app_dir / "individual_report.html")
        new_summary = copy_file_if_exists(row.sharing_summary_csv, app_dir / "sharing_summary.csv")
        new_recs = copy_file_if_exists(row.sharing_recommendations_csv, app_dir / "sharing_recommendations.csv")
        new_log = copy_file_if_exists(row.log_file, app_dir / "app_scan.log")

        packaged_rows.append(
            replace(
                row,
                html_report_path=new_html or row.html_report_path,
                sharing_summary_csv=new_summary or row.sharing_summary_csv,
                sharing_recommendations_csv=new_recs or row.sharing_recommendations_csv,
                log_file=new_log or row.log_file,
            )
        )

    packaged_master_csv = master_dir / "exb_app_inspector_master_summary.csv"
    packaged_master_html = master_dir / "exb_app_inspector_master_report.html"
    packaged_batch_log = logs_dir / "run_multi_exb_inspection.log"

    write_csv(packaged_master_csv, packaged_rows)
    generate_master_html(packaged_rows, packaged_master_html, batch_id)
    copy_file_if_exists(str(batch_log), packaged_batch_log)

    # Also copy the original timestamped master outputs for traceability.
    copy_file_if_exists(str(master_csv), master_dir / master_csv.name)
    copy_file_if_exists(str(master_html), master_dir / master_html.name)

    return {
        "batch_root": batch_root,
        "packaged_master_csv": packaged_master_csv,
        "packaged_master_html": packaged_master_html,
        "packaged_batch_log": packaged_batch_log,
    }


# -----------------------------------------------------------------------------
# CLI
# -----------------------------------------------------------------------------

def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run the LHI ExB App Inspector pipeline for multiple Experience Builder apps."
    )
    parser.add_argument(
        "--apps-csv",
        required=True,
        help="CSV containing app_item_id or app_url values.",
    )
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
        help="Pass --auth-first to Script 06/04. Default remains anonymous-first.",
    )
    parser.add_argument(
        "--ignore-definition-expression",
        action="store_true",
        help="Pass --ignore-definition-expression to Script 06/04.",
    )
    parser.add_argument(
        "--stop-on-error",
        action="store_true",
        help="Stop the batch when an app scan fails. Default is to continue.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print commands without running them.",
    )

    return parser.parse_args()


def main() -> int:
    batch_id = dt.now().strftime("%Y%m%d_%H%M%S")
    log_path = setup_logging(batch_id)
    args = parse_args()

    try:
        script06_path = Path(SCRIPT_06)
        if not script06_path.exists():
            raise FileNotFoundError(f"Required Script 06 not found: {script06_path}")

        apps_path = Path(args.apps_csv)
        if not apps_path.exists():
            raise FileNotFoundError(f"Apps CSV not found: {apps_path}")

        app_rows = read_csv_dicts(apps_path)
        validate_apps(app_rows)

        env = build_child_env(args)

        master_rows: List[MultiAppMasterRow] = []

        print(f"\n=== LHI ExB App Inspector: Multi-App Scan Started ===")
        print(f"Batch ID: {batch_id}")
        print(f"Apps to scan: {len(app_rows)}")

        for index, row in enumerate(app_rows, start=1):
            try:
                app_info = get_app_identifier(row)
                print(f"\n--- App {index}/{len(app_rows)}: {app_info['app_name']} ---")

                scan_result = run_script06_for_app(
                    app_info=app_info,
                    args=args,
                    env=env,
                    batch_id=batch_id,
                )

                master_row = build_master_row_from_outputs(
                    app_info=app_info,
                    batch_id=batch_id,
                    scan_result=scan_result,
                )
                master_rows.append(master_row)

                if scan_result["returncode"] != 0 and args.stop_on_error:
                    raise RuntimeError(scan_result["error"])

            except Exception as app_exc:
                logging.exception("App scan failed: %s", app_exc)

                fallback_info = {
                    "item_id": row.get("app_item_id") or row.get("item_id") or "",
                    "app_url": row.get("app_url") or "",
                    "app_name": row.get("app_name") or row.get("name") or row.get("title") or "Unknown app",
                    "notes": row.get("notes") or "",
                }

                master_rows.append(
                    MultiAppMasterRow(
                        scan_timestamp_utc=now_utc_string(),
                        batch_id=batch_id,
                        app_input_name=fallback_info["app_name"],
                        app_item_id=fallback_info["item_id"],
                        app_url=fallback_info["app_url"],
                        app_title="",
                        app_access="",
                        webmap_count="",
                        webmap_access_summary="",
                        layer_count="",
                        public_reachable_layer_count="",
                        authenticated_layer_count="",
                        inaccessible_layer_count="",
                        active_dependency_count="",
                        active_dependency_layer_count="",
                        template_residue_dependency_count="",
                        possible_broken_dependency_count="",
                        overall_status="failed",
                        overall_risk_level="critical",
                        overall_risk_score="",
                        scan_status="failed",
                        failed_stage_code="",
                        failed_stage_name="row/setup",
                        error_message=str(app_exc),
                        sharing_summary_csv="",
                        sharing_recommendations_csv="",
                        html_report_path="",
                        log_file="",
                        notes=fallback_info["notes"],
                    )
                )

                if args.stop_on_error:
                    raise

        master_csv = MULTI_APP_DIR / f"exb_app_inspector_master_summary_{batch_id}.csv"
        master_html = MULTI_APP_DIR / f"exb_app_inspector_master_report_{batch_id}.html"

        write_csv(master_csv, master_rows)
        generate_master_html(master_rows, master_html, batch_id)

        packaged_outputs = package_batch_outputs(
            rows=master_rows,
            master_csv=master_csv,
            master_html=master_html,
            batch_log=log_path,
            batch_id=batch_id,
        )

        success_count = sum(1 for r in master_rows if r.scan_status == "success")
        failed_count = len(master_rows) - success_count

        print("\n=== LHI ExB App Inspector: Multi-App Scan Complete ===")
        print(f"Apps processed: {len(master_rows)}")
        print(f"Successful: {success_count}")
        print(f"Failed: {failed_count}")
        print(f"Master CSV: {master_csv}")
        print(f"Master HTML report: {master_html}")
        print(f"Batch log: {log_path}")
        print("\nPackaged batch folder:")
        print(f"Batch root: {packaged_outputs['batch_root']}")
        print(f"Packaged master CSV: {packaged_outputs['packaged_master_csv']}")
        print(f"Packaged master HTML report: {packaged_outputs['packaged_master_html']}")
        print(f"Packaged batch log: {packaged_outputs['packaged_batch_log']}")

        return 0 if failed_count == 0 else 2

    except Exception as exc:
        logging.exception("Multi-app inspection failed: %s", exc)
        print("\nMulti-app inspection failed. Check the log file for details:")
        print(log_path)
        print(f"Error: {exc}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
