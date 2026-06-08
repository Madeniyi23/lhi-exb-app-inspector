
"""
LHI ExB App Inspector - Streamlit Local UI v0.1

Purpose:
- Provide a simple local UI over the existing ExB App Inspector scripts.
- Discover Experience Builder apps using Script 09.
- Preview discovered apps.
- Run multi-app inspections using Script 07.
- Preview/download packaged batch outputs.

This UI does not replace the script engine. It wraps it.
"""

from __future__ import annotations

import os
import sys
import time
import zipfile
import subprocess
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import pandas as pd
import streamlit as st


APP_TITLE = "LHI ExB App Inspector"
APP_VERSION = "Streamlit MVP v0.1.3"


# -----------------------------------------------------------------------------
# Helpers
# -----------------------------------------------------------------------------

def project_root() -> Path:
    return Path.cwd()


def outputs_dir(root: Path) -> Path:
    return root / "outputs"


def csv_dir(root: Path) -> Path:
    return outputs_dir(root) / "csv"


def batches_dir(root: Path) -> Path:
    return outputs_dir(root) / "batches"


def reports_dir(root: Path) -> Path:
    return outputs_dir(root) / "reports"


def ensure_output_dirs(root: Path) -> None:
    for folder in [
        outputs_dir(root),
        csv_dir(root),
        outputs_dir(root) / "logs",
        reports_dir(root),
        outputs_dir(root) / "multi_app",
        batches_dir(root),
    ]:
        folder.mkdir(parents=True, exist_ok=True)


def latest_file(folder: Path, pattern: str, since_ts: Optional[float] = None) -> Optional[Path]:
    if not folder.exists():
        return None

    files = list(folder.glob(pattern))
    if since_ts is not None:
        files = [p for p in files if p.stat().st_mtime >= since_ts]

    if not files:
        return None

    return max(files, key=lambda p: p.stat().st_mtime)


def latest_batch_folder(root: Path, since_ts: Optional[float] = None) -> Optional[Path]:
    folder = batches_dir(root)
    if not folder.exists():
        return None

    candidates = [p for p in folder.iterdir() if p.is_dir()]
    if since_ts is not None:
        candidates = [p for p in candidates if p.stat().st_mtime >= since_ts]

    if not candidates:
        return None

    return max(candidates, key=lambda p: p.stat().st_mtime)


def list_csv_files(root: Path, pattern: str) -> List[Path]:
    folder = csv_dir(root)
    if not folder.exists():
        return []
    return sorted(folder.glob(pattern), key=lambda p: p.stat().st_mtime, reverse=True)


def list_batch_folders(root: Path) -> List[Path]:
    folder = batches_dir(root)
    if not folder.exists():
        return []
    return sorted([p for p in folder.iterdir() if p.is_dir()], key=lambda p: p.stat().st_mtime, reverse=True)


def read_csv_preview(path: Path, max_rows: int = 500) -> pd.DataFrame:
    try:
        return pd.read_csv(path).head(max_rows)
    except Exception as exc:
        st.warning(f"Could not read CSV: {path}\n\n{exc}")
        return pd.DataFrame()


def zip_folder(folder: Path, zip_path: Path) -> Path:
    if zip_path.exists():
        zip_path.unlink()

    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as z:
        for file in folder.rglob("*"):
            if file.is_file():
                z.write(file, file.relative_to(folder.parent))

    return zip_path


def command_to_text(cmd: List[str]) -> str:
    return " ".join(f'"{c}"' if " " in str(c) else str(c) for c in cmd)


def format_seconds(seconds: float) -> str:
    try:
        seconds = int(round(float(seconds)))
    except Exception:
        return ""
    hours, remainder = divmod(seconds, 3600)
    minutes, secs = divmod(remainder, 60)
    if hours:
        return f"{hours}h {minutes}m {secs}s"
    if minutes:
        return f"{minutes}m {secs}s"
    return f"{secs}s"


def estimate_duration_from_master_csv(df: pd.DataFrame) -> str:
    if df.empty or "scan_timestamp_utc" not in df.columns:
        return ""
    try:
        ts = pd.to_datetime(df["scan_timestamp_utc"], errors="coerce", utc=True).dropna()
        if len(ts) < 2:
            return ""
        return format_seconds((ts.max() - ts.min()).total_seconds())
    except Exception:
        return ""


def file_uri(path: Path) -> str:
    try:
        return path.resolve().as_uri()
    except Exception:
        return str(path)

def open_local_path(path: Path) -> None:
    """
    Opens a local file/folder on the machine running Streamlit.
    This avoids browser blocking of file:/// links from localhost.
    """
    try:
        os.startfile(str(path.resolve()))  # Windows only
    except Exception as exc:
        st.warning(f"Could not open local path automatically: {exc}")




def run_command_live(
    cmd: List[str],
    cwd: Path,
    env: Dict[str, str],
    title: str,
) -> Tuple[int, str]:
    """
    Runs a command and streams output into the UI.
    """
    st.subheader(title)
    st.code(command_to_text(cmd), language="bat")

    output_box = st.empty()
    progress_note = st.empty()

    collected: List[str] = []

    process = subprocess.Popen(
        cmd,
        cwd=str(cwd),
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        bufsize=1,
        universal_newlines=True,
    )

    last_update = time.time()

    while True:
        line = process.stdout.readline() if process.stdout else ""
        if line:
            collected.append(line.rstrip())
            # Keep UI responsive; show last 120 lines.
            output_box.text("\n".join(collected[-120:]))
            last_update = time.time()

        if process.poll() is not None:
            break

        if time.time() - last_update > 2:
            progress_note.caption(f"Still running... {datetime.now().strftime('%H:%M:%S')}")
            last_update = time.time()

        time.sleep(0.05)

    # Drain any remaining output.
    if process.stdout:
        remaining = process.stdout.read()
        if remaining:
            collected.extend(remaining.splitlines())

    output_box.text("\n".join(collected[-200:]))
    return_code = process.returncode or 0

    if return_code == 0:
        st.success(f"{title} completed successfully.")
    else:
        st.error(f"{title} failed with return code {return_code}.")

    return return_code, "\n".join(collected)


def build_env(password: str) -> Dict[str, str]:
    env = os.environ.copy()
    if password:
        env["LHI_ARCGIS_PASSWORD"] = password
    return env


def validate_scripts(root: Path) -> Dict[str, bool]:
    required = [
        "07_run_multi_exb_inspection.py",
        "09_discover_exb_apps.py",
        "06_run_full_exb_inspection.py",
        "01_scan_exb_app_metadata.py",
    ]
    return {name: (root / name).exists() for name in required}


# -----------------------------------------------------------------------------
# UI
# -----------------------------------------------------------------------------

st.set_page_config(
    page_title=APP_TITLE,
    page_icon="🧭",
    layout="wide",
)

st.title("🧭 LHI ExB App Inspector")
st.caption(APP_VERSION)

root = project_root()
ensure_output_dirs(root)

with st.sidebar:
    st.header("Connection")

    portal = st.text_input(
        "Portal URL",
        value="https://spatialsolutions.maps.arcgis.com/",
        help="ArcGIS Online or ArcGIS Enterprise portal URL.",
    )

    username = st.text_input(
        "Username",
        value="",
        help="Portal username. Password is only stored in this Streamlit session environment.",
    )

    password = st.text_input(
        "Password",
        value="",
        type="password",
        help="Not written to disk. Passed to scripts through LHI_ARCGIS_PASSWORD environment variable.",
    )

    st.divider()

    st.header("Runtime")

    python_exe = st.text_input(
        "Python executable",
        value=sys.executable,
        help="Use ArcGIS Pro Python when running locally from the ArcGIS Pro environment.",
    )

    st.caption(f"Project folder: `{root}`")

    scripts_ok = validate_scripts(root)
    missing = [name for name, exists in scripts_ok.items() if not exists]

    if missing:
        st.error("Missing required scripts:\n\n" + "\n".join(f"- {m}" for m in missing))
    else:
        st.success("Required scripts found.")


tab_discover, tab_inspect, tab_results, tab_about = st.tabs(
    ["1️⃣ Discover Apps", "2️⃣ Run Inspection", "3️⃣ Review Results", "ℹ️ About"]
)


# -----------------------------------------------------------------------------
# Tab 1: Discover
# -----------------------------------------------------------------------------

with tab_discover:
    st.header("Discover Experience Builder Apps")

    st.write(
        "Use Script 09 to discover Experience Builder apps in the connected organization "
        "and create a Script 07-ready input CSV."
    )

    col1, col2, col3 = st.columns(3)

    with col1:
        mode = st.selectbox("Discovery mode", ["standard", "broad", "exhaustive"], index=1)
        item_type = st.selectbox("Item type", ["web-experience-only", "all", "templates-only"], index=0)

    with col2:
        status = st.selectbox(
            "Status",
            ["published-or-changed", "published", "changed", "draft", "all", "unknown"],
            index=0,
        )
        exclude_templates = st.checkbox("Exclude templates", value=True)

    with col3:
        max_total = st.number_input("Max total per query", min_value=50, max_value=5000, value=1000, step=50)
        limit = st.number_input("Limit output apps", min_value=1, max_value=1000, value=50, step=1)

    owner_filter = st.text_input("Optional owner filter", value="", help="Comma-separated owner usernames.")
    access_filter = st.text_input("Optional access filter", value="", help="public, org, private, shared")
    search_filter = st.text_input("Optional search text", value="", help="Optional search term to narrow discovery.")

    run_discovery = st.button("🔎 Discover Apps", type="primary")

    if run_discovery:
        if missing:
            st.stop()

        if not username:
            st.warning("Enter a username before running discovery.")
            st.stop()

        start_ts = time.time()

        cmd = [
            python_exe,
            "09_discover_exb_apps.py",
            "--portal",
            portal,
            "--username",
            username,
            "--mode",
            mode,
            "--max-total-per-query",
            str(int(max_total)),
            "--item-type",
            item_type,
            "--status",
            status,
            "--limit",
            str(int(limit)),
            "--write-excluded-candidates",
        ]

        if exclude_templates:
            cmd.append("--exclude-templates")
        if owner_filter.strip():
            cmd.extend(["--owner", owner_filter.strip()])
        if access_filter.strip():
            cmd.extend(["--access", access_filter.strip()])
        if search_filter.strip():
            cmd.extend(["--search", search_filter.strip()])

        rc, _out = run_command_live(
            cmd=cmd,
            cwd=root,
            env=build_env(password),
            title="Running Script 09 - Discover ExB Apps",
        )

        if rc == 0:
            inventory = latest_file(csv_dir(root), "discovered_exb_apps_inventory_*.csv", since_ts=start_ts)
            input_csv = latest_file(csv_dir(root), "discovered_exb_apps_input_*.csv", since_ts=start_ts)
            summary_csv = latest_file(csv_dir(root), "discovered_exb_apps_summary_*.csv", since_ts=start_ts)

            st.session_state["latest_inventory_csv"] = str(inventory) if inventory else ""
            st.session_state["latest_input_csv"] = str(input_csv) if input_csv else ""
            st.session_state["latest_discovery_summary_csv"] = str(summary_csv) if summary_csv else ""

            st.success("Discovery outputs captured.")
            st.write("Script 07 input CSV:", input_csv)
            st.write("Inventory CSV:", inventory)

    latest_inventory = st.session_state.get("latest_inventory_csv") or ""
    if latest_inventory and Path(latest_inventory).exists():
        st.subheader("Latest Discovery Preview")
        df = read_csv_preview(Path(latest_inventory), max_rows=500)
        st.dataframe(df, use_container_width=True, height=420)

        if not df.empty:
            col_a, col_b, col_c, col_d = st.columns(4)
            col_a.metric("Apps in preview", len(df))
            if "exb_status" in df.columns:
                col_b.metric("Published", int((df["exb_status"].astype(str).str.lower() == "published").sum()))
                col_c.metric("Changed", int((df["exb_status"].astype(str).str.lower() == "changed").sum()))
            if "owner" in df.columns:
                col_d.metric("Owners", df["owner"].nunique())


# -----------------------------------------------------------------------------
# Tab 2: Inspect
# -----------------------------------------------------------------------------

with tab_inspect:
    st.header("Run Multi-App Inspection")

    st.write("Use Script 07 to scan apps from a Script 07 input CSV.")
    st.info("The UI passes your sidebar username/password to Script 07 so it does not wait for hidden command-line input.")

    discovered_inputs = list_csv_files(root, "discovered_exb_apps_input_*.csv")
    manual_inputs = list_csv_files(root, "input_apps*.csv")
    all_inputs = discovered_inputs + [p for p in manual_inputs if p not in discovered_inputs]

    latest_input_default = st.session_state.get("latest_input_csv") or ""
    input_options = [str(p) for p in all_inputs]

    if latest_input_default and latest_input_default not in input_options:
        input_options.insert(0, latest_input_default)

    if input_options:
        apps_csv = st.selectbox("Apps CSV", input_options, index=0)
    else:
        apps_csv = st.text_input("Apps CSV path", value="")

    st.caption("The CSV should contain `app_item_id`; optional columns include `app_name` and `notes`.")

    run_scan = st.button("🚀 Run Inspection", type="primary")

    if run_scan:
        if missing:
            st.stop()

        if not apps_csv or not Path(apps_csv).exists():
            st.warning("Select a valid apps CSV first.")
            st.stop()

        if not username:
            st.warning("Enter a username before running inspection.")
            st.stop()

        start_ts = time.time()

        cmd = [
            python_exe,
            "07_run_multi_exb_inspection.py",
            "--portal",
            portal,
            "--apps-csv",
            apps_csv,
            "--username",
            username,
        ]

        rc, _out = run_command_live(
            cmd=cmd,
            cwd=root,
            env=build_env(password),
            title="Running Script 07 - Multi-App Inspection",
        )

        end_ts = time.time()
        elapsed = end_ts - start_ts
        st.session_state["latest_scan_duration_seconds"] = elapsed
        st.session_state["latest_scan_duration_text"] = format_seconds(elapsed)

        if rc == 0:
            st.success(f"Inspection runtime: {format_seconds(elapsed)}")
            batch = latest_batch_folder(root, since_ts=start_ts)
            if batch:
                st.session_state["latest_batch_folder"] = str(batch)
                st.success(f"Latest batch folder: {batch}")
            else:
                st.warning("Inspection completed but no new batch folder was found.")
        else:
            st.warning(f"Inspection stopped after: {format_seconds(elapsed)}")


# -----------------------------------------------------------------------------
# Tab 3: Results
# -----------------------------------------------------------------------------

with tab_results:
    st.header("Review Results")

    batch_folders = list_batch_folders(root)
    latest_batch_default = st.session_state.get("latest_batch_folder") or ""

    batch_options = [str(p) for p in batch_folders]
    if latest_batch_default and latest_batch_default not in batch_options:
        batch_options.insert(0, latest_batch_default)

    if not batch_options:
        st.info("No batch folders found yet.")
    else:
        selected_batch = Path(st.selectbox("Batch folder", batch_options, index=0))

        master_csv = selected_batch / "master" / "exb_app_inspector_master_summary.csv"
        master_html = selected_batch / "master" / "exb_app_inspector_master_report.html"

        col1, col2 = st.columns(2)
        col1.write("Master CSV:")
        col1.code(str(master_csv))
        col2.write("Master HTML:")
        col2.code(str(master_html))

        if master_html.exists():
            col_open_1, col_open_2, col_open_3 = st.columns([1, 1, 2])

            with col_open_1:
                if st.button("🌐 Open master HTML report", key=f"open_master_{selected_batch.name}"):
                    open_local_path(master_html)

            with col_open_2:
                if st.button("📁 Open batch folder", key=f"open_folder_{selected_batch.name}"):
                    open_local_path(selected_batch)

            with col_open_3:
                st.caption("Open the report from the local batch folder so links to individual reports keep working.")

            with open(master_html, "rb") as f:
                st.download_button(
                    label="⬇️ Download master HTML only",
                    data=f,
                    file_name=master_html.name,
                    mime="text/html",
                    help="HTML-only download does not include the app folders, so individual report links may not work from this downloaded copy.",
                )

        if master_csv.exists():
            df_master = read_csv_preview(master_csv, max_rows=1000)

            if not df_master.empty:
                st.subheader("Master Summary Preview")

                session_duration = st.session_state.get("latest_scan_duration_text", "")
                estimated_duration = estimate_duration_from_master_csv(df_master)
                duration_text = session_duration or estimated_duration or "Not available"

                metric_cols = st.columns(6)
                metric_cols[0].metric("Apps", len(df_master))

                if "scan_status" in df_master.columns:
                    metric_cols[1].metric("Successful", int((df_master["scan_status"].astype(str).str.lower() == "success").sum()))
                    metric_cols[2].metric("Failed", int((df_master["scan_status"].astype(str).str.lower() != "success").sum()))

                if "overall_risk_level" in df_master.columns:
                    metric_cols[3].metric("Critical", int((df_master["overall_risk_level"].astype(str).str.lower() == "critical").sum()))
                    metric_cols[4].metric("High", int((df_master["overall_risk_level"].astype(str).str.lower() == "high").sum()))

                metric_cols[5].metric("Runtime", duration_text)

                st.dataframe(df_master, use_container_width=True, height=500)

                if "scan_status" in df_master.columns:
                    failed = df_master[df_master["scan_status"].astype(str).str.lower() != "success"]
                    if not failed.empty:
                        st.subheader("Failed / Non-successful Apps")
                        st.dataframe(failed, use_container_width=True)

        if master_html.exists():
            st.info("For working individual report links, open the master HTML from the local batch folder or download/extract the full batch ZIP. Downloading only the master HTML will not include the app report folders.")

        zip_name = f"{selected_batch.name}.zip"
        temp_zip = selected_batch.parent / zip_name

        if st.button("📦 Create downloadable ZIP for selected batch"):
            zip_folder(selected_batch, temp_zip)
            st.session_state["selected_batch_zip"] = str(temp_zip)

        batch_zip = st.session_state.get("selected_batch_zip", "")
        if batch_zip and Path(batch_zip).exists():
            with open(batch_zip, "rb") as f:
                st.download_button(
                    label="Download batch ZIP",
                    data=f,
                    file_name=Path(batch_zip).name,
                    mime="application/zip",
                )


# -----------------------------------------------------------------------------
# Tab 4: About
# -----------------------------------------------------------------------------

with tab_about:
    st.header("About this MVP")

    st.write(
        """
        This Streamlit MVP is a thin local UI over the existing LHI ExB App Inspector engine.
        It is intentionally simple: it reduces command-line friction without rewriting the backend.
        """
    )

    st.subheader("Current workflow")
    st.code(
        """
        1. Discover apps with Script 09
        2. Preview discovered inventory
        3. Run Script 07 against selected/generated input CSV
        4. Review packaged batch results
        """,
        language="text",
    )

    st.subheader("Next UI improvements")
    st.write(
        """
        - Select apps directly from the discovery table
        - Show live per-app progress instead of raw command output
        - Add batch history
        - Embed the master HTML report
        - Add app-level filters for owner, access, risk, and failed stage
        """
    )
