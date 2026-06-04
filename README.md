# LHI ExB App Inspector

**LHI ExB App Inspector** is a Python-based diagnostic tool for auditing ArcGIS Experience Builder applications.

It helps GIS developers, ArcGIS Online administrators, and municipal GIS teams inspect what Experience Builder apps depend on before users discover broken widgets, inaccessible services, sharing mismatches, or internal network dependencies.

**Current status:** `v1.1.2 stability release`

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

---

## Quick start

### 1. Install requirements

```bat
pip install -r requirements.txt
```

If using ArcGIS Pro, run from the `arcgispro-py3` environment.

---

### 2. Discover Experience Builder apps

Example: discover operational apps only, excluding drafts and templates.

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

This creates a Script 07-ready CSV:

```text
outputs/csv/discovered_exb_apps_input_*.csv
```

---

### 3. Run a multi-app scan

```bat
python 07_run_multi_exb_inspection.py ^
  --portal "https://yourorg.maps.arcgis.com/" ^
  --apps-csv "outputs/csv/discovered_exb_apps_input_*.csv"
```

The packaged output will be created here:

```text
outputs/batches/<batch_id>/
```

Open:

```text
outputs/batches/<batch_id>/master/exb_app_inspector_master_report.html
```

---

### 4. Run a single app scan

```bat
python 06_run_full_exb_inspection.py ^
  --portal "https://yourorg.maps.arcgis.com/" ^
  --item-id "YOUR_EXB_APP_ITEM_ID" ^
  --username "YOUR_USERNAME"
```

---

## Packaged batch output

Each batch contains:

```text
outputs/batches/<batch_id>/
├── master/
│   ├── exb_app_inspector_master_summary.csv
│   └── exb_app_inspector_master_report.html
├── apps/
│   └── <app_name_itemid>/
│       ├── individual_report.html
│       ├── sharing_summary.csv
│       ├── sharing_recommendations.csv
│       ├── layer_identity_summary.csv
│       ├── layer_identity_resolution.csv
│       ├── app_scan.log
│       └── logs/
└── logs/
    └── run_multi_exb_inspection.log
```

---

## v1.1.2 stability improvements

This release hardens the engine before moving into the Streamlit/UI phase.

Key improvements:

- Script 01 now uses REST-first metadata/config retrieval and direct token generation.
- Script 06 now enforces hard stage-level subprocess timeouts.
- Scripts 02, 03, 04, 05, and 08 now handle valid zero-row/no-map outputs by writing empty CSV files with headers.
- Script 09 supports org-wide ExB discovery, operational-status filtering, template exclusion, and test-batch limits.
- A 50-app operational test batch completed with 49 successful scans and 1 isolated Stage 03 failure.

---

## Internal service detection

The classifier distinguishes real internal/environment indicators from ordinary business terms.

It flags patterns such as:

```text
itvpgisappint
General_Int
internal
appint
gisappint
arcgisint
/dev/
test-server
/uat/
staging
sandbox
```

It avoids false positives from normal business words such as:

```text
Development
Development Applications
Development Planning
```

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

## Roadmap

Planned next phases:

- Streamlit local UI
- Better report viewer inside the UI
- Batch history and comparison
- AGOL dashboard export table
- Performance scoring
- User/group access comparison
- Stage 03 crash hardening / REST-first web map reads

---

## Project principle

**Build fast. Think deeply. Publish strategically.**

Lazy Hat Innovations
