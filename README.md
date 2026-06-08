# LHI ExB App Inspector

**LHI ExB App Inspector** is a Python and Streamlit-based diagnostic tool for auditing ArcGIS Experience Builder applications.

It helps GIS developers, ArcGIS Online administrators, and municipal GIS teams inspect what Experience Builder apps depend on before users discover broken widgets, inaccessible services, sharing mismatches, or internal network dependencies.

**Current status:** `v1.1.3 report clarity release`

---

## What it does

The tool scans Experience Builder apps and reports on:

- App metadata and sharing
- Experience Builder widgets and data sources
- Web map references
- Web map layers and tables
- Active widget dependencies
- Template/copy residue
- REST service health
- Internal/dev/test service indicators
- App/web map/layer sharing compatibility
- Layer identity resolution
- Failed-stage diagnostics
- Multi-app portfolio summaries
- Shareable batch output folders
- Org-wide Experience Builder discovery
- Local Streamlit UI for discovery, inspection, and report review

---

## Pipeline

The full single-app pipeline is:

```text
01 → 02 → 03 → 04 → 08 → 05
```

| Script | Purpose |
|---|---|
| `01_scan_exb_app_metadata.py` | Reads Experience Builder item metadata and config JSON using REST-first retrieval |
| `02_extract_exb_dependencies.py` | Extracts widgets, data sources, dependencies, and web map references |
| `03_scan_webmap_layers.py` | Reads referenced web maps and resolves active layers vs template residue |
| `04_check_layer_health.py` | Tests REST endpoints, query support, response behavior, and internal host patterns |
| `08_resolve_layer_identity.py` | Resolves exact layer identity by item ID, service URL, title, owner, and authoritative candidates |
| `05_check_sharing_compatibility.py` | Checks sharing/access compatibility and creates individual HTML reports |
| `06_run_full_exb_inspection.py` | Runs the full pipeline for one app with hard stage timeouts |
| `07_run_multi_exb_inspection.py` | Runs the full pipeline for many apps and creates a packaged master report |
| `09_discover_exb_apps.py` | Discovers Experience Builder apps across the organization and creates Script 07 input CSVs |
| `app.py` | Streamlit local UI for discovery, scan execution, and result review |

---

## Quick start: Streamlit UI

### 1. Install UI requirements

```bat
pip install -r requirements_streamlit.txt
```

If ArcGIS Pro Python is locked down, run Streamlit from a separate Python environment and set the ArcGIS Pro Python path in the sidebar.

Example:

```bat
"C:\GIS\python-3.14.5-embed-amd64\python.exe" -m streamlit run app.py
```

### 2. Run the UI

```bat
streamlit run app.py
```

or:

```bat
python -m streamlit run app.py
```

### 3. In the sidebar

Set:

```text
Portal URL
Username
Password
Python executable = C:\Program Files\ArcGIS\Pro\bin\Python\envs\arcgispro-py3\python.exe
```

### 4. Use the workflow tabs

```text
1. Discover Apps
2. Run Inspection
3. Review Results
```

The Review Results tab can open the local master HTML report and batch folder.

---

## Quick start: command line

### Discover Experience Builder apps

```bat
python 09_discover_exb_apps.py ^
  --portal "https://yourorg.maps.arcgis.com/" ^
  --username "YOUR_USERNAME" ^
  --mode broad ^
  --max-total-per-query 1000 ^
  --item-type web-experience-only ^
  --status published-or-changed ^
  --exclude-templates ^
  --limit 50
```

### Run a multi-app scan

```bat
python 07_run_multi_exb_inspection.py ^
  --portal "https://yourorg.maps.arcgis.com/" ^
  --username "YOUR_USERNAME" ^
  --apps-csv "outputs/csv/discovered_exb_apps_input_*.csv"
```

Open:

```text
outputs/batches/<batch_id>/master/exb_app_inspector_master_report.html
```

---

## v1.1.3 report clarity improvements

This release focuses on making the reports more accurate and less misleading for internal/org-only apps.

Key improvements:

- Refined internal dependency risk classification.
- Public app/web map + unreachable internal active layer remains critical.
- Org/shared/private app + unreachable internal active layer is now a high-priority validation item.
- Internal unreachable layer with no active dependency is treated as review.
- Renamed report columns:
  - `Health Risk` → `Endpoint Health`
  - `Severity` → `Action Severity`
- Blank endpoint health values now show as `not checked`.
- Added report interpretation notes explaining scanner context, endpoint health, action severity, and operational risk.
- Streamlit UI v0.1.3 improves local report opening with:
  - Open master HTML report
  - Open batch folder
  - Clearer guidance about full batch ZIPs vs single downloaded HTML files

---

## Security notes

Do not commit real outputs if they contain:

- Internal service URLs
- App names
- Layer names
- Item IDs
- Organization-specific metadata
- Logs
- Raw JSON files

The `.gitignore` excludes outputs and common sensitive/local files by default.

---

## Project principle

**Build fast. Think deeply. Publish strategically.**

Lazy Hat Innovations
