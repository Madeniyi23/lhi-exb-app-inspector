# Workflow

## Full single-app workflow

```text
01_scan_exb_app_metadata
→ 02_extract_exb_dependencies
→ 03_scan_webmap_layers
→ 04_check_layer_health
→ 08_resolve_layer_identity
→ 05_check_sharing_compatibility
```

Use `06_run_full_exb_inspection.py` to run this pipeline.

## Multi-app workflow

```text
09_discover_exb_apps
→ input_apps.csv
→ 07_run_multi_exb_inspection
→ outputs/batches/<batch_id>
```

## Recommended batch process

1. Use Script 09 to discover operational apps.
2. Start with `--limit 50`.
3. Run Script 07 against the generated input CSV.
4. Review failures, critical apps, and internal service risks.
5. Increase to 100+ apps after the 50-app test is stable.

## Recommended review order

1. Open the packaged master HTML.
2. Review failed scans.
3. Review critical/high risk apps.
4. Expand app rows for diagnosis.
5. Open individual app reports when needed.
6. Review layer identity CSVs for ambiguous or internal service layers.
