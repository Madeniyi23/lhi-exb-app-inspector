# LHI ExB App Inspector

**LHI ExB App Inspector** is a Python-based diagnostic tool for auditing ArcGIS Experience Builder applications.

It helps GIS developers, ArcGIS Online administrators, and municipal GIS teams inspect what Experience Builder apps depend on before users discover broken widgets, inaccessible services, sharing mismatches, or internal network dependencies.

**Current status:** `v1.1 stable MVP`

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

---

## Pipeline

The full single-app pipeline is:

```text
01 → 02 → 03 → 04 → 08 → 05
```

| Script | Purpose |
|---|---|
| `01_scan_exb_app_metadata.py` | Reads Experience Builder item metadata and config JSON |
| `02_extract_exb_dependencies.py` | Extracts widgets, data sources, dependencies, and web map references |
| `03_scan_webmap_layers.py` | Reads referenced web maps and resolves active layers vs template residue |
| `04_check_layer_health.py` | Tests REST endpoints, query support, response behavior, and internal host patterns |
| `08_resolve_layer_identity.py` | Resolves exact layer identity by item ID, service URL, title, owner, and authoritative candidates |
| `05_check_sharing_compatibility.py` | Checks sharing/access compatibility and creates individual HTML reports |
| `06_run_full_exb_inspection.py` | Runs the full pipeline for one app |
| `07_run_multi_exb_inspection.py` | Runs the full pipeline for many apps and creates a packaged master report |

---

## Quick start

### 1. Install requirements

```bat
pip install -r requirements.txt
```

If using ArcGIS Pro, run from the `arcgispro-py3` environment.

---

### 2. Prepare an input CSV

Example:

```csv
app_item_id,notes
7bd1b4f0533244a994c7f8c5a1bcc1db,Example app
xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx,Another app
```

A starter file is included:

```text
examples/input_apps_template.csv
```

---

### 3. Run a single app scan

```bat
python 06_run_full_exb_inspection.py ^
  --portal "https://yourorg.maps.arcgis.com/" ^
  --item-id "YOUR_EXB_APP_ITEM_ID"
```

Or provide the username:

```bat
python 06_run_full_exb_inspection.py ^
  --portal "https://yourorg.maps.arcgis.com/" ^
  --item-id "YOUR_EXB_APP_ITEM_ID" ^
  --username "YOUR_USERNAME"
```

The password is requested securely through `getpass`.

---

### 4. Run a multi-app scan

```bat
python 07_run_multi_exb_inspection.py ^
  --portal "https://yourorg.maps.arcgis.com/" ^
  --apps-csv "examples/input_apps_template.csv"
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

This folder is the easiest output to zip, archive, or share.

---

## Key finding types

The inspector can identify:

- Active widget dependencies tied to inaccessible layers
- Public web maps referencing internal services
- App/web map/layer sharing mismatch
- Internal or VPN-only service references
- Ambiguous same-title authoritative layer candidates
- URL-only ArcGIS Server layers without portal item matches
- Template residue copied from old app templates
- Failed stage and exact error message for troubleshooting

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

- Org-wide Experience Builder discovery
- AGOL dashboard export table
- Performance scoring
- User/group access comparison
- HTML identity detail sections
- CLI polish and installer-friendly packaging

---

## Project principle

**Build fast. Think deeply. Publish strategically.**

Lazy Hat Innovations
