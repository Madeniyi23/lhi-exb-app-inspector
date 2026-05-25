"""
LHI ExB App Inspector
Script 02: Extract Experience Builder Dependencies

Purpose:
- Read the raw Experience Builder app JSON created by Script 01
- Extract widget inventory
- Extract data source inventory
- Extract web map / web scene references
- Extract widget-to-data-source dependencies
- Detect missing/orphan data source references
- Detect pending layout/widget placements
- Export clean CSVs for the next inspection stage

Author: Lazy Hat Innovations
"""

from __future__ import annotations

import argparse
import csv
import json
import logging
import re
import sys
from collections import Counter, defaultdict
from dataclasses import dataclass, asdict
from datetime import datetime as dt
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Set, Tuple


# -----------------------------------------------------------------------------
# Defaults
# -----------------------------------------------------------------------------

OUTPUT_ROOT = Path("outputs")
CSV_DIR = OUTPUT_ROOT / "csv"
LOG_DIR = OUTPUT_ROOT / "logs"


# -----------------------------------------------------------------------------
# Data models
# -----------------------------------------------------------------------------

@dataclass
class WidgetInventoryRow:
    app_config_file: str
    widget_id: str
    widget_label: str
    widget_uri: str
    widget_type: str
    widget_version: str
    use_data_sources_count: int
    use_data_sources: str
    output_data_sources_count: int
    output_data_sources: str
    use_map_widget_ids: str
    child_widgets: str
    has_config: bool
    has_layouts: bool
    issue_summary: str


@dataclass
class DataSourceInventoryRow:
    app_config_file: str
    data_source_id: str
    data_source_label: str
    data_source_type: str
    item_id: str
    url: str
    layer_id: str
    source_label: str
    root_data_source_id: str
    main_data_source_id: str
    is_data_in_data_source_instance: str
    is_output_or_temporary: bool
    used_by_widget_count: int
    used_by_widgets: str
    issue_summary: str


@dataclass
class WidgetDataDependencyRow:
    app_config_file: str
    widget_id: str
    widget_label: str
    widget_type: str
    dependency_source: str
    data_source_id: str
    main_data_source_id: str
    root_data_source_id: str
    fields: str
    data_source_exists: bool
    referenced_item_id: str
    referenced_url: str
    issue_summary: str


@dataclass
class WebMapReferenceRow:
    app_config_file: str
    data_source_id: str
    data_source_label: str
    item_id: str
    source_label: str
    used_by_widgets: str
    issue_summary: str


@dataclass
class LayoutIssueRow:
    app_config_file: str
    layout_id: str
    layout_label: str
    layout_type: str
    content_key: str
    widget_id: str
    is_pending: bool
    bbox: str
    issue_summary: str


@dataclass
class ExtractionSummaryRow:
    app_config_file: str
    scan_timestamp: str
    page_count: int
    layout_count: int
    widget_count: int
    data_source_count: int
    web_map_reference_count: int
    web_scene_reference_count: int
    feature_layer_data_source_count: int
    widget_dependency_count: int
    missing_data_source_reference_count: int
    pending_layout_item_count: int
    likely_output_or_temporary_data_source_count: int
    widget_type_summary: str
    data_source_type_summary: str
    issue_summary: str


# -----------------------------------------------------------------------------
# Setup and utility functions
# -----------------------------------------------------------------------------

def setup_output_dirs() -> None:
    for folder in [OUTPUT_ROOT, CSV_DIR, LOG_DIR]:
        folder.mkdir(parents=True, exist_ok=True)


def setup_logging() -> Path:
    setup_output_dirs()
    timestamp = dt.now().strftime("%Y%m%d_%H%M%S")
    log_path = LOG_DIR / f"extract_exb_dependencies_{timestamp}.log"

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s | %(levelname)s | %(message)s",
        handlers=[
            logging.FileHandler(log_path, encoding="utf-8"),
            logging.StreamHandler(sys.stdout),
        ],
    )
    return log_path


def now_string() -> str:
    return dt.now().strftime("%Y-%m-%d %H:%M:%S")


def load_json(path: Path) -> Dict[str, Any]:
    logging.info("Reading ExB config JSON: %s", path)
    with path.open("r", encoding="utf-8") as f:
        data = json.load(f)
    if not isinstance(data, dict):
        raise ValueError("The supplied JSON file does not contain a JSON object at the top level.")
    return data


def safe_str(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, (dict, list)):
        return json.dumps(value, ensure_ascii=False)
    return str(value)


def join_values(values: Iterable[Any]) -> str:
    cleaned = []
    for value in values:
        if value is None:
            continue
        text = str(value).strip()
        if text:
            cleaned.append(text)
    return "; ".join(cleaned)


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


def get_widget_type(uri: str) -> str:
    """
    Converts widget URI to a readable widget type.

    Example:
    widgets/common/list/ -> list
    widgets/arcgis/arcgis-map/ -> arcgis-map
    """
    if not uri:
        return ""
    uri = uri.strip("/")
    if not uri:
        return ""
    return uri.split("/")[-1]


def get_nested_value(data: Dict[str, Any], possible_keys: List[str]) -> Any:
    for key in possible_keys:
        if key in data:
            return data[key]
    return None


def summarize_counter(counter: Counter) -> str:
    return "; ".join(f"{key}: {value}" for key, value in sorted(counter.items()))


def is_likely_output_or_temporary_data_source(data_source_id: str, ds: Dict[str, Any]) -> bool:
    dsid = (data_source_id or "").lower()
    label = safe_str(ds.get("label") or ds.get("sourceLabel") or "").lower()
    ds_type = safe_str(ds.get("type") or "").lower()

    output_patterns = [
        "output",
        "_output_",
        "default_geocode_utility",
        "near_me",
        "proximity",
        "analysis",
        "selection",
        "runtime",
    ]

    if any(pattern in dsid for pattern in output_patterns):
        return True
    if any(pattern in label for pattern in output_patterns):
        return True
    if "output" in ds_type:
        return True

    return False


def normalize_data_source_ref(ref: Any) -> Dict[str, Any]:
    """
    ExB useDataSources entries are usually dicts, but this function protects
    against unexpected string or null values.
    """
    if isinstance(ref, dict):
        return ref
    if isinstance(ref, str):
        return {"dataSourceId": ref}
    return {}


def extract_data_source_id_from_ref(ref: Dict[str, Any]) -> str:
    return safe_str(ref.get("dataSourceId") or ref.get("id") or "")


def extract_fields_from_ref(ref: Dict[str, Any]) -> str:
    fields = ref.get("fields")
    if isinstance(fields, list):
        return join_values(fields)
    return safe_str(fields)


def find_data_source_references_in_text(text: str) -> Set[str]:
    """
    Finds data-dsid references embedded in text/HTML config.
    Example: data-dsid="dataSource_3-ServiceAmenities_4613"
    """
    refs = set()
    if not text:
        return refs

    patterns = [
        r'data-dsid=["\']([^"\']+)["\']',
        r'%22dataSourceId%22%3A%22([^%]+)%22',
        r'"dataSourceId"\s*:\s*"([^"]+)"',
    ]

    for pattern in patterns:
        for match in re.findall(pattern, text):
            if match:
                refs.add(match)
    return refs


def collect_embedded_data_source_refs(obj: Any) -> Set[str]:
    """
    Recursively walks config values to detect embedded data source references,
    especially in Text widgets where dynamic expressions are stored as HTML.
    """
    refs: Set[str] = set()

    if isinstance(obj, dict):
        for value in obj.values():
            refs.update(collect_embedded_data_source_refs(value))
    elif isinstance(obj, list):
        for value in obj:
            refs.update(collect_embedded_data_source_refs(value))
    elif isinstance(obj, str):
        refs.update(find_data_source_references_in_text(obj))

    return refs


# -----------------------------------------------------------------------------
# Extraction functions
# -----------------------------------------------------------------------------

def extract_widgets(config: Dict[str, Any], app_config_file: str) -> List[WidgetInventoryRow]:
    widgets = config.get("widgets", {})
    if not isinstance(widgets, dict):
        logging.warning("No widgets dictionary found in config.")
        return []

    rows: List[WidgetInventoryRow] = []

    for widget_id, widget in widgets.items():
        if not isinstance(widget, dict):
            continue

        uri = safe_str(widget.get("uri"))
        widget_type = get_widget_type(uri)
        use_data_sources = widget.get("useDataSources", []) or []
        output_data_sources = widget.get("outputDataSources", []) or []
        use_map_widget_ids = widget.get("useMapWidgetIds", []) or []
        child_widgets = widget.get("widgets", []) or []

        use_ds_ids = []
        for ref in use_data_sources:
            ref_obj = normalize_data_source_ref(ref)
            ds_id = extract_data_source_id_from_ref(ref_obj)
            if ds_id:
                use_ds_ids.append(ds_id)

        output_ds_ids = []
        for ref in output_data_sources:
            if isinstance(ref, str):
                output_ds_ids.append(ref)
            elif isinstance(ref, dict):
                output_ds_ids.append(extract_data_source_id_from_ref(ref))

        issues = []
        if not uri:
            issues.append("Widget URI is missing.")
        if not widget.get("label"):
            issues.append("Widget label is missing.")
        if use_data_sources and not use_ds_ids:
            issues.append("Widget has useDataSources, but no dataSourceId could be extracted.")

        rows.append(
            WidgetInventoryRow(
                app_config_file=app_config_file,
                widget_id=safe_str(widget.get("id") or widget_id),
                widget_label=safe_str(widget.get("label")),
                widget_uri=uri,
                widget_type=widget_type,
                widget_version=safe_str(widget.get("version")),
                use_data_sources_count=len(use_ds_ids),
                use_data_sources=join_values(use_ds_ids),
                output_data_sources_count=len(output_ds_ids),
                output_data_sources=join_values(output_ds_ids),
                use_map_widget_ids=join_values(use_map_widget_ids),
                child_widgets=join_values(child_widgets),
                has_config=isinstance(widget.get("config"), dict),
                has_layouts=isinstance(widget.get("layouts"), dict),
                issue_summary=" | ".join(issues),
            )
        )

    return rows


def extract_data_sources(
    config: Dict[str, Any],
    app_config_file: str,
    widget_dependency_map: Dict[str, Set[str]],
) -> List[DataSourceInventoryRow]:
    data_sources = config.get("dataSources", {})
    if not isinstance(data_sources, dict):
        logging.warning("No dataSources dictionary found in config.")
        return []

    rows: List[DataSourceInventoryRow] = []

    for data_source_id, ds in data_sources.items():
        if not isinstance(ds, dict):
            continue

        ds_type = safe_str(ds.get("type"))
        item_id = safe_str(
            ds.get("itemId")
            or ds.get("portalItemId")
            or ds.get("itemIdOfDataSource")
            or ""
        )
        url = safe_str(ds.get("url") or ds.get("serviceUrl") or "")
        layer_id = safe_str(ds.get("layerId") or ds.get("layer") or ds.get("dataSourceJson", {}).get("layerId") if isinstance(ds.get("dataSourceJson"), dict) else "")
        root_data_source_id = safe_str(ds.get("rootDataSourceId"))
        main_data_source_id = safe_str(ds.get("mainDataSourceId"))
        label = safe_str(ds.get("label"))
        source_label = safe_str(ds.get("sourceLabel"))
        is_output = is_likely_output_or_temporary_data_source(data_source_id, ds)
        used_by_widgets = sorted(widget_dependency_map.get(data_source_id, set()))

        issues = []
        if not ds_type:
            issues.append("Data source type is missing.")
        if ds_type.upper() in {"WEB_MAP", "WEB_SCENE"} and not item_id:
            issues.append("Web map/web scene data source does not have an item ID.")
        if ds_type.upper() in {"FEATURE_LAYER", "SCENE_LAYER"} and not item_id and not url:
            issues.append("Layer data source has no item ID or URL.")
        if not used_by_widgets and not is_output:
            issues.append("Data source is not directly referenced by any widget useDataSources entry.")

        rows.append(
            DataSourceInventoryRow(
                app_config_file=app_config_file,
                data_source_id=safe_str(data_source_id),
                data_source_label=label,
                data_source_type=ds_type,
                item_id=item_id,
                url=url,
                layer_id=layer_id,
                source_label=source_label,
                root_data_source_id=root_data_source_id,
                main_data_source_id=main_data_source_id,
                is_data_in_data_source_instance=safe_str(ds.get("isDataInDataSourceInstance")),
                is_output_or_temporary=is_output,
                used_by_widget_count=len(used_by_widgets),
                used_by_widgets=join_values(used_by_widgets),
                issue_summary=" | ".join(issues),
            )
        )

    return rows


def build_widget_dependency_map(config: Dict[str, Any]) -> Dict[str, Set[str]]:
    widgets = config.get("widgets", {})
    dependency_map: Dict[str, Set[str]] = defaultdict(set)

    if not isinstance(widgets, dict):
        return dependency_map

    for widget_id, widget in widgets.items():
        if not isinstance(widget, dict):
            continue

        use_data_sources = widget.get("useDataSources", []) or []
        for ref in use_data_sources:
            ref_obj = normalize_data_source_ref(ref)
            ds_id = extract_data_source_id_from_ref(ref_obj)
            if ds_id:
                dependency_map[ds_id].add(safe_str(widget.get("id") or widget_id))

        embedded_refs = collect_embedded_data_source_refs(widget.get("config", {}))
        for ds_id in embedded_refs:
            dependency_map[ds_id].add(safe_str(widget.get("id") or widget_id))

    return dependency_map


def extract_widget_dependencies(
    config: Dict[str, Any],
    app_config_file: str,
) -> List[WidgetDataDependencyRow]:
    widgets = config.get("widgets", {})
    data_sources = config.get("dataSources", {})

    if not isinstance(widgets, dict):
        return []
    if not isinstance(data_sources, dict):
        data_sources = {}

    rows: List[WidgetDataDependencyRow] = []

    for widget_id, widget in widgets.items():
        if not isinstance(widget, dict):
            continue

        widget_id_clean = safe_str(widget.get("id") or widget_id)
        widget_label = safe_str(widget.get("label"))
        widget_type = get_widget_type(safe_str(widget.get("uri")))

        # Standard useDataSources dependencies
        use_data_sources = widget.get("useDataSources", []) or []
        for ref in use_data_sources:
            ref_obj = normalize_data_source_ref(ref)
            ds_id = extract_data_source_id_from_ref(ref_obj)
            if not ds_id:
                continue

            ds = data_sources.get(ds_id, {}) if isinstance(data_sources, dict) else {}
            ds_exists = isinstance(ds, dict) and bool(ds)

            issues = []
            if not ds_exists:
                issues.append("Referenced data source does not exist in config.dataSources.")

            rows.append(
                WidgetDataDependencyRow(
                    app_config_file=app_config_file,
                    widget_id=widget_id_clean,
                    widget_label=widget_label,
                    widget_type=widget_type,
                    dependency_source="useDataSources",
                    data_source_id=ds_id,
                    main_data_source_id=safe_str(ref_obj.get("mainDataSourceId")),
                    root_data_source_id=safe_str(ref_obj.get("rootDataSourceId")),
                    fields=extract_fields_from_ref(ref_obj),
                    data_source_exists=ds_exists,
                    referenced_item_id=safe_str(ds.get("itemId") if isinstance(ds, dict) else ""),
                    referenced_url=safe_str(ds.get("url") if isinstance(ds, dict) else ""),
                    issue_summary=" | ".join(issues),
                )
            )

        # Embedded dynamic expression dependencies, often in Text widgets
        embedded_refs = collect_embedded_data_source_refs(widget.get("config", {}))
        standard_refs = {
            extract_data_source_id_from_ref(normalize_data_source_ref(ref))
            for ref in use_data_sources
        }

        for ds_id in sorted(ref for ref in embedded_refs if ref and ref not in standard_refs):
            ds = data_sources.get(ds_id, {}) if isinstance(data_sources, dict) else {}
            ds_exists = isinstance(ds, dict) and bool(ds)

            issues = []
            if not ds_exists:
                issues.append("Embedded data source reference does not exist in config.dataSources.")

            rows.append(
                WidgetDataDependencyRow(
                    app_config_file=app_config_file,
                    widget_id=widget_id_clean,
                    widget_label=widget_label,
                    widget_type=widget_type,
                    dependency_source="embedded_config_expression",
                    data_source_id=ds_id,
                    main_data_source_id=safe_str(ds.get("mainDataSourceId") if isinstance(ds, dict) else ""),
                    root_data_source_id=safe_str(ds.get("rootDataSourceId") if isinstance(ds, dict) else ""),
                    fields="",
                    data_source_exists=ds_exists,
                    referenced_item_id=safe_str(ds.get("itemId") if isinstance(ds, dict) else ""),
                    referenced_url=safe_str(ds.get("url") if isinstance(ds, dict) else ""),
                    issue_summary=" | ".join(issues),
                )
            )

    return rows


def extract_webmap_references(
    data_source_rows: List[DataSourceInventoryRow],
    app_config_file: str,
) -> List[WebMapReferenceRow]:
    rows: List[WebMapReferenceRow] = []

    for ds in data_source_rows:
        ds_type_upper = ds.data_source_type.upper()
        if ds_type_upper not in {"WEB_MAP", "WEB_SCENE"}:
            continue

        issues = []
        if not ds.item_id:
            issues.append("Missing web map/web scene item ID.")
        if not ds.used_by_widgets:
            issues.append("Web map/web scene is not directly referenced by any widget.")

        rows.append(
            WebMapReferenceRow(
                app_config_file=app_config_file,
                data_source_id=ds.data_source_id,
                data_source_label=ds.data_source_label,
                item_id=ds.item_id,
                source_label=ds.source_label,
                used_by_widgets=ds.used_by_widgets,
                issue_summary=" | ".join(issues),
            )
        )

    return rows


def extract_layout_issues(config: Dict[str, Any], app_config_file: str) -> List[LayoutIssueRow]:
    layouts = config.get("layouts", {})
    if not isinstance(layouts, dict):
        return []

    rows: List[LayoutIssueRow] = []

    for layout_id, layout in layouts.items():
        if not isinstance(layout, dict):
            continue

        content = layout.get("content", {})
        if not isinstance(content, dict):
            continue

        for content_key, content_item in content.items():
            if not isinstance(content_item, dict):
                continue

            is_pending = bool(content_item.get("isPending"))
            bbox = content_item.get("bbox", {})
            bbox_text = safe_str(bbox)
            issues = []

            if is_pending:
                issues.append("Layout content item is marked as pending.")
            if "NaN" in bbox_text:
                issues.append("Layout bounding box contains NaN value.")
            if content_item.get("type") == "WIDGET" and not content_item.get("widgetId"):
                issues.append("Layout content item is a widget but has no widgetId.")

            if not issues:
                continue

            rows.append(
                LayoutIssueRow(
                    app_config_file=app_config_file,
                    layout_id=safe_str(layout_id),
                    layout_label=safe_str(layout.get("label")),
                    layout_type=safe_str(layout.get("type")),
                    content_key=safe_str(content_key),
                    widget_id=safe_str(content_item.get("widgetId")),
                    is_pending=is_pending,
                    bbox=bbox_text,
                    issue_summary=" | ".join(issues),
                )
            )

    return rows


def create_summary_row(
    config: Dict[str, Any],
    app_config_file: str,
    widget_rows: List[WidgetInventoryRow],
    data_source_rows: List[DataSourceInventoryRow],
    widget_dependency_rows: List[WidgetDataDependencyRow],
    webmap_rows: List[WebMapReferenceRow],
    layout_issue_rows: List[LayoutIssueRow],
) -> ExtractionSummaryRow:
    pages = config.get("pages", {})
    layouts = config.get("layouts", {})

    widget_type_counter = Counter(row.widget_type or "UNKNOWN" for row in widget_rows)
    ds_type_counter = Counter(row.data_source_type or "UNKNOWN" for row in data_source_rows)

    missing_ds_ref_count = sum(1 for row in widget_dependency_rows if not row.data_source_exists)
    pending_layout_count = sum(1 for row in layout_issue_rows if row.is_pending)
    likely_output_count = sum(1 for row in data_source_rows if row.is_output_or_temporary)
    feature_layer_count = sum(1 for row in data_source_rows if row.data_source_type.upper() == "FEATURE_LAYER")
    web_scene_count = sum(1 for row in data_source_rows if row.data_source_type.upper() == "WEB_SCENE")

    issues = []
    if not webmap_rows:
        issues.append("No web map or web scene references were found.")
    if missing_ds_ref_count:
        issues.append(f"{missing_ds_ref_count} widget data source references are missing from config.dataSources.")
    if pending_layout_count:
        issues.append(f"{pending_layout_count} layout items are marked as pending.")

    return ExtractionSummaryRow(
        app_config_file=app_config_file,
        scan_timestamp=now_string(),
        page_count=len(pages) if isinstance(pages, dict) else 0,
        layout_count=len(layouts) if isinstance(layouts, dict) else 0,
        widget_count=len(widget_rows),
        data_source_count=len(data_source_rows),
        web_map_reference_count=len(webmap_rows),
        web_scene_reference_count=web_scene_count,
        feature_layer_data_source_count=feature_layer_count,
        widget_dependency_count=len(widget_dependency_rows),
        missing_data_source_reference_count=missing_ds_ref_count,
        pending_layout_item_count=pending_layout_count,
        likely_output_or_temporary_data_source_count=likely_output_count,
        widget_type_summary=summarize_counter(widget_type_counter),
        data_source_type_summary=summarize_counter(ds_type_counter),
        issue_summary=" | ".join(issues),
    )


# -----------------------------------------------------------------------------
# CLI
# -----------------------------------------------------------------------------

def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Extract widget, data source, and web map dependencies from raw Experience Builder JSON."
    )
    parser.add_argument(
        "--input-json",
        required=True,
        help="Path to the raw ExB JSON file created by Script 01.",
    )
    parser.add_argument(
        "--output-prefix",
        default=None,
        help="Optional output filename prefix. Defaults to the input JSON stem.",
    )
    return parser.parse_args()


def main() -> int:
    log_path = setup_logging()
    args = parse_args()

    try:
        input_path = Path(args.input_json)
        if not input_path.exists():
            raise FileNotFoundError(f"Input JSON not found: {input_path}")

        app_config_file = input_path.name
        output_prefix = args.output_prefix or input_path.stem
        timestamp = dt.now().strftime("%Y%m%d_%H%M%S")

        logging.info("Starting LHI ExB App Inspector - Script 02")
        logging.info("Input JSON: %s", input_path)

        config = load_json(input_path)

        widget_dependency_map = build_widget_dependency_map(config)
        widget_rows = extract_widgets(config, app_config_file)
        data_source_rows = extract_data_sources(config, app_config_file, widget_dependency_map)
        widget_dependency_rows = extract_widget_dependencies(config, app_config_file)
        webmap_rows = extract_webmap_references(data_source_rows, app_config_file)
        layout_issue_rows = extract_layout_issues(config, app_config_file)
        summary_row = create_summary_row(
            config=config,
            app_config_file=app_config_file,
            widget_rows=widget_rows,
            data_source_rows=data_source_rows,
            widget_dependency_rows=widget_dependency_rows,
            webmap_rows=webmap_rows,
            layout_issue_rows=layout_issue_rows,
        )

        summary_csv = CSV_DIR / f"exb_extraction_summary_{output_prefix}_{timestamp}.csv"
        widgets_csv = CSV_DIR / f"widget_inventory_{output_prefix}_{timestamp}.csv"
        data_sources_csv = CSV_DIR / f"data_source_inventory_{output_prefix}_{timestamp}.csv"
        dependencies_csv = CSV_DIR / f"widget_data_dependencies_{output_prefix}_{timestamp}.csv"
        webmaps_csv = CSV_DIR / f"webmap_references_{output_prefix}_{timestamp}.csv"
        layout_issues_csv = CSV_DIR / f"layout_issues_{output_prefix}_{timestamp}.csv"

        write_csv(summary_csv, [summary_row])
        write_csv(widgets_csv, widget_rows)
        write_csv(data_sources_csv, data_source_rows)
        write_csv(dependencies_csv, widget_dependency_rows)
        write_csv(webmaps_csv, webmap_rows)
        write_csv(layout_issues_csv, layout_issue_rows)

        print("\n=== LHI ExB App Inspector: Script 02 Complete ===")
        print(f"Input JSON: {input_path}")
        print(f"Pages: {summary_row.page_count}")
        print(f"Layouts: {summary_row.layout_count}")
        print(f"Widgets: {summary_row.widget_count}")
        print(f"Data sources: {summary_row.data_source_count}")
        print(f"Web map references: {summary_row.web_map_reference_count}")
        print(f"Feature layer data sources: {summary_row.feature_layer_data_source_count}")
        print(f"Widget-data dependencies: {summary_row.widget_dependency_count}")
        print(f"Missing data source references: {summary_row.missing_data_source_reference_count}")
        print(f"Pending layout items: {summary_row.pending_layout_item_count}")
        print("\nOutputs:")
        print(f"Summary CSV: {summary_csv}")
        print(f"Widget inventory CSV: {widgets_csv}")
        print(f"Data source inventory CSV: {data_sources_csv}")
        print(f"Widget dependencies CSV: {dependencies_csv}")
        print(f"Web map references CSV: {webmaps_csv}")
        print(f"Layout issues CSV: {layout_issues_csv}")
        print(f"Log file: {log_path}")

        if summary_row.issue_summary:
            print("\nWarnings:")
            print(summary_row.issue_summary)

        return 0

    except Exception as exc:
        logging.exception("Extraction failed: %s", exc)
        print("\nExtraction failed. Check the log file for details:")
        print(log_path)
        print(f"Error: {exc}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
