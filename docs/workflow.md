# Workflow

## Streamlit workflow

```text
1. Run Streamlit
2. Enter portal credentials
3. Discover Experience Builder apps
4. Preview the discovery table
5. Run multi-app inspection
6. Review the packaged batch output
7. Open master HTML report or batch folder
```

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


## Selective inspection workflow

```text
1. Run Script 09 from the Discover Apps tab.
2. Review the discovered apps table.
3. Filter by owner, access, status, or text search.
4. Select apps manually or use all filtered rows.
5. Create a selected-apps CSV.
6. In Run Inspection, choose "Selected apps from UI".
7. Run Script 07 against the selected-apps CSV.
```
