# Workflow

The LHI ExB App Inspector follows a staged inspection workflow.

## Single-app workflow

```text
01_scan_exb_app_metadata
→ 02_extract_exb_dependencies
→ 03_scan_webmap_layers
→ 04_check_layer_health
→ 05_check_sharing_compatibility
```

Use `06_run_full_exb_inspection.py` to run the full single-app workflow automatically.

## Multi-app workflow

```text
input_apps.csv
→ 07_run_multi_exb_inspection.py
→ packaged batch report
```

The multi-app runner calls Script 06 for each app and creates a master HTML report.

## Recommended usage

Start with 2–3 apps, validate the reports, then scan a larger portfolio.

For large batches, review:

- failed scans
- critical/high risk apps
- inaccessible layers
- internal service references
- active widget dependency failures
- template residue counts
