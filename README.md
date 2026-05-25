# LHI ExB App Inspector

**LHI ExB App Inspector** is a Python-based diagnostic tool for auditing ArcGIS Experience Builder applications.

It helps GIS developers, ArcGIS Online administrators, and municipal GIS teams answer practical questions such as:

- Which web maps and layers does this Experience Builder app depend on?
- Are active widgets connected to valid web map layers?
- Are REST services reachable and queryable?
- Are app, web map, and layer sharing levels compatible?
- Is the app referencing internal, dev, test, or VPN-only services?
- Which apps need review before migration, publication, or cleanup?

This is an early **v1.0-alpha / MVP** release from Lazy Hat Innovations.

---

## Core capabilities

- Single-app inspection
- Multi-app inspection from CSV
- App metadata scan
- Experience Builder dependency extraction
- Web map layer scan
- Layer REST health check
- Sharing compatibility check
- Template residue detection
- Internal service / internal host detection
- Failed-stage reporting
- Individual HTML reports
- Multi-app master HTML report
- Batch output packaging for sharing
- Dashboard-ready CSV outputs

---

## Quick start

### 1. Install requirements

Use an ArcGIS Pro Python environment or another environment with the ArcGIS API for Python installed.

```bat
pip install -r requirements.txt
```

If using ArcGIS Pro, you may already have `arcgis` available in the `arcgispro-py3` environment.

---

### 2. Prepare an app list

Create a CSV like this:

```csv
app_item_id,notes
7bd1b4f0533244a994c7f8c5a1bcc1db,Example app
xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx,Another app
```

A starter file is included here:

```text
examples/input_apps_template.csv
```

---

### 3. Run a single app inspection

```bat
python 06_run_full_exb_inspection.py ^
  --portal "https://yourorg.maps.arcgis.com/" ^
  --item-id "YOUR_EXB_APP_ITEM_ID"
```

The script will ask for your ArcGIS username and password once.

You can also provide the username:

```bat
python 06_run_full_exb_inspection.py ^
  --portal "https://yourorg.maps.arcgis.com/" ^
  --item-id "YOUR_EXB_APP_ITEM_ID" ^
  --username "YOUR_USERNAME"
```

---

### 4. Run a multi-app inspection

```bat
python 07_run_multi_exb_inspection.py ^
  --portal "https://yourorg.maps.arcgis.com/" ^
  --apps-csv "examples/input_apps_template.csv"
```

At the end, open the packaged master report:

```text
outputs/batches/<batch_id>/master/exb_app_inspector_master_report.html
```

---

## Output structure

The tool writes standard outputs to:

```text
outputs/csv/
outputs/logs/
outputs/reports/
outputs/multi_app/
```

Script 07 also creates a clean packaged batch folder:

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
│       └── app_scan.log
└── logs/
    └── run_multi_exb_inspection.log
```

This packaged batch folder is the easiest output to zip, archive, or share.

---

## Script overview

| Script | Purpose |
|---|---|
| `01_scan_exb_app_metadata.py` | Reads Experience Builder item metadata and config JSON |
| `02_extract_exb_dependencies.py` | Extracts widgets, data sources, dependencies, and web map references |
| `03_scan_webmap_layers.py` | Reads referenced web maps and resolves active layers vs template residue |
| `04_check_layer_health.py` | Tests REST endpoints, query support, response time, and internal host patterns |
| `05_check_sharing_compatibility.py` | Compares app/web map/layer sharing and produces individual HTML reports |
| `06_run_full_exb_inspection.py` | Runs Scripts 01–05 for one app |
| `07_run_multi_exb_inspection.py` | Runs Script 06 for many apps and creates a packaged master report |

---

## Important notes

- Do not commit real `outputs/` folders if they contain internal URLs, app names, service URLs, or organizational data.
- Passwords are not written to disk. The runner prompts once and passes the password through a temporary environment variable for child scripts.
- The tool does not permanently modify ArcGIS Online, Enterprise, web maps, layers, or Experience Builder apps. It is read-only.
- Internal host detection is a governance signal, not proof of a broken service. Review the app audience, network/VPN requirements, and service sharing before making changes.

---

## Typical findings

The tool can help identify:

- Apps with missing or inaccessible REST services
- Public web maps referencing internal service URLs
- Active widgets pointing to unavailable layers
- Harmless copied-template residue
- Apps that work for some users but not others due to sharing or network access
- Apps that need deeper migration or cleanup review

---

## Roadmap

Planned next features:

- Script 08: Layer Identity Resolver
- Script 09: Org-wide Experience Builder app discovery
- Script 10: AGOL dashboard export table
- Performance scoring improvements
- User/group access comparison
- Cleaner CLI packaging

---

## Project principle

**Build fast. Think deeply. Publish strategically.**

Lazy Hat Innovations
