"""
LHI ExB App Inspector
Script 03: Scan Web Map Layers

Purpose:
- Read web map references exported by Script 02
- Connect to ArcGIS Online or ArcGIS Enterprise
- Fetch each referenced web map / web scene item
- Read the web map JSON
- Extract operational layers and tables
- Extract layer URLs, item IDs, visibility, popups, filters, and scale settings
- Compare ExB widget data source references to actual web map layer IDs where possible
- Classify unresolved references intelligently:
    * resolved_root_webmap
    * resolved_active_webmap_layer
    * unresolved_likely_template_residue
    * unresolved_possible_broken_dependency
    * unresolved_output_or_runtime_reference
    * needs_manual_review

Author: Lazy Hat Innovations
"""

from __future__ import annotations

import argparse
import csv
import getpass
import json
import logging
import os
import re
import sys
from dataclasses import dataclass, asdict
from datetime import datetime as dt, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Set, Tuple

try:
    from arcgis.gis import GIS
except ImportError:
    GIS = None


# -----------------------------------------------------------------------------
# Defaults
# -----------------------------------------------------------------------------

OUTPUT_ROOT = Path("outputs")
CSV_DIR = OUTPUT_ROOT / "csv"
RAW_JSON_DIR = OUTPUT_ROOT / "raw_json"
LOG_DIR = OUTPUT_ROOT / "logs"
DEFAULT_PORTAL = "https://www.arcgis.com"


# -----------------------------------------------------------------------------
# Data models
# -----------------------------------------------------------------------------

@dataclass
class WebMapSummaryRow:
    scan_timestamp_utc: str
    portal_url: str
    webmap_item_id: str
    webmap_title: str
    item_type: str
    owner: str
    created_utc: str
    modified_utc: str
    access: str
    group_count: int
    homepage: str
    url: str
    operational_layer_count: int
    table_count: int
    basemap_layer_count: int
    readable_json: bool
    raw_json_path: str
    issue_summary: str


@dataclass
class WebMapLayerRow:
    scan_timestamp_utc: str
    webmap_item_id: str
    webmap_title: str
    layer_index: int
    layer_id: str
    layer_title: str
    layer_type: str
    url: str
    item_id: str
    layer_definition_id: str
    parent_layer_id: str
    visibility: str
    opacity: str
    min_scale: str
    max_scale: str
    has_popup: bool
    popup_title: str
    has_definition_expression: bool
    definition_expression: str
    layer_source_type: str
    is_group_layer: bool
    sublayer_count: int
    source_json_path: str
    issue_summary: str


@dataclass
class WebMapTableRow:
    scan_timestamp_utc: str
    webmap_item_id: str
    webmap_title: str
    table_index: int
    table_id: str
    table_title: str
    table_type: str
    url: str
    item_id: str
    layer_definition_id: str
    has_popup: bool
    popup_title: str
    has_definition_expression: bool
    definition_expression: str
    source_json_path: str
    issue_summary: str


@dataclass
class ExBLayerReferenceResolutionRow:
    scan_timestamp_utc: str
    exb_dependency_file: str
    webmap_item_id: str
    widget_id: str
    widget_label: str
    widget_type: str
    dependency_source: str
    exb_data_source_id: str
    exb_root_data_source_id: str
    exb_main_data_source_id: str
    exb_fields: str
    data_source_exists_in_exb_config: str
    resolved_webmap_layer_id: str
    resolved_webmap_layer_title: str
    resolved_webmap_layer_url: str
    resolved_webmap_layer_item_id: str
    resolution_status: str
    severity: str
    issue_summary: str
    recommendation: str


# -----------------------------------------------------------------------------
# Setup and utilities
# -----------------------------------------------------------------------------

def setup_output_dirs() -> None:
    for folder in [OUTPUT_ROOT, CSV_DIR, RAW_JSON_DIR, LOG_DIR]:
        folder.mkdir(parents=True, exist_ok=True)


def setup_logging() -> Path:
    setup_output_dirs()
    timestamp = dt.now().strftime("%Y%m%d_%H%M%S")
    log_path = LOG_DIR / f"scan_webmap_layers_{timestamp}.log"

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s | %(levelname)s | %(message)s",
        handlers=[
            logging.FileHandler(log_path, encoding="utf-8"),
            logging.StreamHandler(sys.stdout),
        ],
    )
    return log_path


def now_utc_string() -> str:
    return dt.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")


def utc_from_esri_millis(value: Optional[int]) -> str:
    if value is None:
        return ""
    try:
        return dt.fromtimestamp(value / 1000, tz=timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
    except Exception:
        return ""


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


def sanitize_filename(value: str) -> str:
    value = re.sub(r"[^A-Za-z0-9_\-]+", "_", value.strip())
    return value[:120] if value else "untitled"


def normalize_token(value: str) -> str:
    return (value or "").strip().lower()


def normalize_for_match(value: str) -> str:
    value = normalize_token(value)
    value = re.sub(r"[^a-z0-9]+", "_", value)
    value = re.sub(r"_+", "_", value).strip("_")
    return value


def read_csv_dicts(path: Path) -> List[Dict[str, str]]:
    logging.info("Reading CSV: %s", path)
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


def save_raw_json(prefix: str, item_id: str, data: Dict[str, Any]) -> Path:
    timestamp = dt.now().strftime("%Y%m%d_%H%M%S")
    filename = f"{sanitize_filename(prefix)}_{item_id}_{timestamp}.json"
    output_path = RAW_JSON_DIR / filename
    with output_path.open("w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
    return output_path


def get_group_count(item: Any) -> int:
    try:
        groups = item.shared_with.get("groups", []) if item.shared_with else []
        return len(groups)
    except Exception:
        return 0


def extract_item_ids_from_webmap_references(rows: List[Dict[str, str]]) -> List[str]:
    item_ids: List[str] = []
    seen: Set[str] = set()

    for row in rows:
        item_id = (row.get("item_id") or row.get("webmap_item_id") or "").strip()
        if item_id and item_id not in seen:
            item_ids.append(item_id)
            seen.add(item_id)

    return item_ids


def connect_to_portal(portal_url: str, username: Optional[str], anonymous: bool) -> Any:
    if GIS is None:
        raise ImportError("The arcgis package is not installed. Install it with: pip install arcgis")

    if anonymous:
        logging.info("Connecting anonymously to portal: %s", portal_url)
        return GIS(portal_url)

    if not username:
        logging.info("No username supplied. Connecting anonymously to portal: %s", portal_url)
        return GIS(portal_url)

    password = os.getenv("LHI_ARCGIS_PASSWORD")
    if not password:
        password = getpass.getpass(f"Password for {username}: ")
    logging.info("Connecting as %s to portal: %s", username, portal_url)
    return GIS(portal_url, username, password)


def fetch_item(gis: Any, item_id: str) -> Any:
    logging.info("Fetching item: %s", item_id)
    item = gis.content.get(item_id)
    if item is None:
        raise RuntimeError(f"Item not found or not accessible: {item_id}")
    return item


def read_item_data(item: Any) -> Tuple[Optional[Dict[str, Any]], str]:
    try:
        data = item.get_data()
        if data is None:
            return None, "Item data returned None."
        if not isinstance(data, dict):
            return None, f"Item data is not a JSON object. Returned type: {type(data).__name__}"
        return data, ""
    except Exception as exc:
        return None, f"Could not read item JSON: {exc}"


# -----------------------------------------------------------------------------
# Web map JSON parsing
# -----------------------------------------------------------------------------

def get_layer_popup_info(layer: Dict[str, Any]) -> Tuple[bool, str]:
    popup_info = layer.get("popupInfo")
    if isinstance(popup_info, dict):
        return True, safe_str(popup_info.get("title"))
    return False, ""


def get_definition_expression(layer: Dict[str, Any]) -> Tuple[bool, str]:
    candidates = []

    if layer.get("definitionExpression"):
        candidates.append(layer.get("definitionExpression"))

    layer_def = layer.get("layerDefinition")
    if isinstance(layer_def, dict) and layer_def.get("definitionExpression"):
        candidates.append(layer_def.get("definitionExpression"))

    if candidates:
        return True, join_values(candidates)
    return False, ""


def get_layer_definition_id(layer: Dict[str, Any]) -> str:
    layer_def = layer.get("layerDefinition")
    if isinstance(layer_def, dict):
        return safe_str(layer_def.get("id"))
    return ""


def get_layer_item_id(layer: Dict[str, Any]) -> str:
    item_id = layer.get("itemId") or layer.get("portalItemId") or ""
    if item_id:
        return safe_str(item_id)

    item = layer.get("item")
    if isinstance(item, dict):
        return safe_str(item.get("itemId") or item.get("id"))

    return ""


def get_layer_type(layer: Dict[str, Any]) -> str:
    if layer.get("layerType"):
        return safe_str(layer.get("layerType"))
    if layer.get("type"):
        return safe_str(layer.get("type"))
    feature_collection = layer.get("featureCollection")
    if isinstance(feature_collection, dict):
        layer_def = feature_collection.get("layerDefinition")
        if isinstance(layer_def, dict):
            return safe_str(layer_def.get("type"))
    return ""


def is_group_layer(layer: Dict[str, Any]) -> bool:
    if layer.get("layerType") == "GroupLayer":
        return True
    if isinstance(layer.get("layers"), list):
        return True
    return False


def sublayer_count(layer: Dict[str, Any]) -> int:
    layers = layer.get("layers")
    if isinstance(layers, list):
        return len(layers)
    return 0


def extract_operational_layers_recursive(
    layers: List[Dict[str, Any]],
    webmap_item_id: str,
    webmap_title: str,
    raw_json_path: Path,
    parent_layer_id: str = "",
    start_index: int = 0,
) -> List[WebMapLayerRow]:
    rows: List[WebMapLayerRow] = []

    for offset, layer in enumerate(layers):
        if not isinstance(layer, dict):
            continue

        layer_index = start_index + offset
        layer_id = safe_str(layer.get("id"))
        title = safe_str(layer.get("title"))
        url = safe_str(layer.get("url"))
        item_id = get_layer_item_id(layer)
        layer_type = get_layer_type(layer)
        layer_def_id = get_layer_definition_id(layer)
        has_popup, popup_title = get_layer_popup_info(layer)
        has_def_expr, def_expr = get_definition_expression(layer)
        group_layer = is_group_layer(layer)
        child_count = sublayer_count(layer)

        issues = []
        if not layer_id:
            issues.append("Layer ID is missing.")
        if not title:
            issues.append("Layer title is missing.")
        if not url and not group_layer and not item_id:
            issues.append("Layer has no URL, no item ID, and is not clearly a group layer.")
        if group_layer:
            issues.append("Group layer detected; child layers are listed separately where available.")

        rows.append(
            WebMapLayerRow(
                scan_timestamp_utc=now_utc_string(),
                webmap_item_id=webmap_item_id,
                webmap_title=webmap_title,
                layer_index=layer_index,
                layer_id=layer_id,
                layer_title=title,
                layer_type=layer_type,
                url=url,
                item_id=item_id,
                layer_definition_id=layer_def_id,
                parent_layer_id=parent_layer_id,
                visibility=safe_str(layer.get("visibility")),
                opacity=safe_str(layer.get("opacity")),
                min_scale=safe_str(layer.get("minScale")),
                max_scale=safe_str(layer.get("maxScale")),
                has_popup=has_popup,
                popup_title=popup_title,
                has_definition_expression=has_def_expr,
                definition_expression=def_expr,
                layer_source_type=safe_str(layer.get("sourceType")),
                is_group_layer=group_layer,
                sublayer_count=child_count,
                source_json_path=str(raw_json_path),
                issue_summary=" | ".join(issues),
            )
        )

        child_layers = layer.get("layers")
        if isinstance(child_layers, list):
            rows.extend(
                extract_operational_layers_recursive(
                    child_layers,
                    webmap_item_id=webmap_item_id,
                    webmap_title=webmap_title,
                    raw_json_path=raw_json_path,
                    parent_layer_id=layer_id,
                    start_index=0,
                )
            )

    return rows


def extract_tables(
    tables: List[Dict[str, Any]],
    webmap_item_id: str,
    webmap_title: str,
    raw_json_path: Path,
) -> List[WebMapTableRow]:
    rows: List[WebMapTableRow] = []

    for index, table in enumerate(tables):
        if not isinstance(table, dict):
            continue

        table_id = safe_str(table.get("id"))
        title = safe_str(table.get("title"))
        url = safe_str(table.get("url"))
        item_id = get_layer_item_id(table)
        table_type = get_layer_type(table)
        layer_def_id = get_layer_definition_id(table)
        has_popup, popup_title = get_layer_popup_info(table)
        has_def_expr, def_expr = get_definition_expression(table)

        issues = []
        if not table_id:
            issues.append("Table ID is missing.")
        if not title:
            issues.append("Table title is missing.")
        if not url and not item_id:
            issues.append("Table has no URL or item ID.")

        rows.append(
            WebMapTableRow(
                scan_timestamp_utc=now_utc_string(),
                webmap_item_id=webmap_item_id,
                webmap_title=webmap_title,
                table_index=index,
                table_id=table_id,
                table_title=title,
                table_type=table_type,
                url=url,
                item_id=item_id,
                layer_definition_id=layer_def_id,
                has_popup=has_popup,
                popup_title=popup_title,
                has_definition_expression=has_def_expr,
                definition_expression=def_expr,
                source_json_path=str(raw_json_path),
                issue_summary=" | ".join(issues),
            )
        )

    return rows


def extract_basemap_layer_count(webmap_data: Dict[str, Any]) -> int:
    base_map = webmap_data.get("baseMap") or webmap_data.get("basemap") or {}
    if not isinstance(base_map, dict):
        return 0
    base_layers = base_map.get("baseMapLayers") or base_map.get("layers") or []
    if isinstance(base_layers, list):
        return len(base_layers)
    return 0


def scan_webmap_item(
    portal_url: str,
    item: Any,
    webmap_data: Optional[Dict[str, Any]],
    raw_json_path: Path,
    data_issue: str,
) -> Tuple[WebMapSummaryRow, List[WebMapLayerRow], List[WebMapTableRow]]:
    webmap_title = safe_str(getattr(item, "title", ""))
    webmap_item_id = safe_str(getattr(item, "id", ""))

    issues = []
    if data_issue:
        issues.append(data_issue)

    if not isinstance(webmap_data, dict):
        webmap_data = {}
        issues.append("Web map JSON could not be read as a dictionary.")

    operational_layers = webmap_data.get("operationalLayers", [])
    if not isinstance(operational_layers, list):
        operational_layers = []
        issues.append("operationalLayers is missing or not a list.")

    tables = webmap_data.get("tables", [])
    if not isinstance(tables, list):
        tables = []
        issues.append("tables is present but not a list.")

    layer_rows = extract_operational_layers_recursive(
        operational_layers,
        webmap_item_id=webmap_item_id,
        webmap_title=webmap_title,
        raw_json_path=raw_json_path,
    )
    table_rows = extract_tables(
        tables,
        webmap_item_id=webmap_item_id,
        webmap_title=webmap_title,
        raw_json_path=raw_json_path,
    )

    summary = WebMapSummaryRow(
        scan_timestamp_utc=now_utc_string(),
        portal_url=portal_url,
        webmap_item_id=webmap_item_id,
        webmap_title=webmap_title,
        item_type=safe_str(getattr(item, "type", "")),
        owner=safe_str(getattr(item, "owner", "")),
        created_utc=utc_from_esri_millis(getattr(item, "created", None)),
        modified_utc=utc_from_esri_millis(getattr(item, "modified", None)),
        access=safe_str(getattr(item, "access", "")),
        group_count=get_group_count(item),
        homepage=safe_str(getattr(item, "homepage", "")),
        url=safe_str(getattr(item, "url", "")),
        operational_layer_count=len(layer_rows),
        table_count=len(table_rows),
        basemap_layer_count=extract_basemap_layer_count(webmap_data),
        readable_json=bool(webmap_data),
        raw_json_path=str(raw_json_path),
        issue_summary=" | ".join(issues),
    )

    return summary, layer_rows, table_rows


# -----------------------------------------------------------------------------
# ExB reference resolution
# -----------------------------------------------------------------------------

def load_optional_widget_dependencies(path: Optional[Path]) -> List[Dict[str, str]]:
    if not path:
        return []
    if not path.exists():
        raise FileNotFoundError(f"Widget dependency CSV not found: {path}")
    return read_csv_dicts(path)


def build_layer_lookup(layer_rows: List[WebMapLayerRow]) -> Dict[str, WebMapLayerRow]:
    lookup: Dict[str, WebMapLayerRow] = {}

    for layer in layer_rows:
        candidates = [
            layer.layer_id,
            layer.layer_title,
            layer.item_id,
            layer.url,
            layer.layer_definition_id,
            normalize_for_match(layer.layer_id),
            normalize_for_match(layer.layer_title),
        ]

        # Also add the last URL path token if available, for MapServer/34 style matching later.
        if layer.url:
            url_parts = layer.url.rstrip("/").split("/")
            if url_parts:
                candidates.append(url_parts[-1])

        for candidate in candidates:
            key = normalize_token(candidate)
            if key and key not in lookup:
                lookup[key] = layer

    return lookup


def is_root_webmap_reference(exb_ds_id: str, root_ds: str, main_ds: str, webmap_data_source_ids: Set[str]) -> bool:
    """
    Returns True only when the ExB data source ID itself is the root web map
    data source.

    Important:
    Layer-derived ExB IDs often have root/main values that point back to the
    web map, for example:
        exb_ds_id = dataSource_3-ServiceAmenities_4613
        root_ds   = dataSource_3
        main_ds   = dataSource_3-ServiceAmenities_4613

    In that case, the reference is a layer-derived reference, not the root map.
    So we only compare exb_ds_id directly to the known web map data source IDs.
    """
    exb_id = normalize_token(exb_ds_id)
    webmap_ids = {normalize_token(v) for v in webmap_data_source_ids if v}
    return bool(exb_id and exb_id in webmap_ids)


def is_likely_output_or_runtime_reference(exb_ds_id: str, widget_type: str, dependency_source: str) -> bool:
    text = f"{exb_ds_id} {widget_type} {dependency_source}".lower()
    patterns = [
        "output",
        "_output_",
        "default_geocode_utility",
        "geocode",
        "near_me",
        "near-me",
        "proximity",
        "runtime",
        "selection",
        "analysis",
    ]
    return any(pattern in text for pattern in patterns)


def resolve_exb_data_source_to_webmap_layer(exb_ds_id: str, layer_lookup: Dict[str, WebMapLayerRow]) -> Tuple[Optional[WebMapLayerRow], str]:
    """
    Best-effort resolution of ExB layer-like data source IDs to web map layers.

    Experience Builder often uses generated IDs like:
    dataSource_3-ServiceAmenities_4613

    The web map may store a layer ID/title like:
    ServiceAmenities_4613
    Service Amenities
    """
    raw_ds_id = exb_ds_id or ""
    ds_id = normalize_token(raw_ds_id)
    ds_id_normalized = normalize_for_match(raw_ds_id)

    if not ds_id:
        return None, "needs_manual_review_empty_data_source_id"

    if ds_id in layer_lookup:
        return layer_lookup[ds_id], "resolved_active_webmap_layer_exact_match"

    if ds_id_normalized in layer_lookup:
        return layer_lookup[ds_id_normalized], "resolved_active_webmap_layer_normalized_match"

    # Try suffix after first hyphen, e.g. dataSource_3-ServiceAmenities_4613 -> ServiceAmenities_4613
    if "-" in raw_ds_id:
        suffix = raw_ds_id.split("-", 1)[1]
        suffix_key = normalize_token(suffix)
        suffix_normalized = normalize_for_match(suffix)

        if suffix_key in layer_lookup:
            return layer_lookup[suffix_key], "resolved_active_webmap_layer_suffix_match"
        if suffix_normalized in layer_lookup:
            return layer_lookup[suffix_normalized], "resolved_active_webmap_layer_suffix_normalized_match"

    # Try lookup key contained in ExB ID.
    for key, layer in layer_lookup.items():
        if key and len(key) >= 6 and key in ds_id:
            return layer, "resolved_active_webmap_layer_contains_layer_key"
        if key and len(key) >= 6 and key in ds_id_normalized:
            return layer, "resolved_active_webmap_layer_contains_normalized_layer_key"

    return None, "unresolved_no_matching_webmap_layer"


def classify_unresolved_reference(
    dep: Dict[str, str],
    webmap_data_source_ids: Set[str],
) -> Tuple[str, str, str, str]:
    """
    Classifies unresolved ExB references into product-safe categories.

    Returns:
    - resolution_status
    - severity
    - issue_summary
    - recommendation
    """
    exb_ds_id = dep.get("data_source_id", "")
    root_ds = dep.get("root_data_source_id", "")
    main_ds = dep.get("main_data_source_id", "")
    dependency_source = dep.get("dependency_source", "")
    widget_type = dep.get("widget_type", "")
    data_source_exists_text = str(dep.get("data_source_exists", "")).strip().lower()
    data_source_exists = data_source_exists_text == "true"

    if is_root_webmap_reference(exb_ds_id, root_ds, main_ds, webmap_data_source_ids):
        return (
            "resolved_root_webmap",
            "info",
            "Reference points to the root web map data source, not an individual layer.",
            "No action required. Use layer-level records for operational layer checks.",
        )

    if is_likely_output_or_runtime_reference(exb_ds_id, widget_type, dependency_source):
        return (
            "unresolved_output_or_runtime_reference",
            "info",
            "Reference appears to be an output, geocoder, proximity, selection, or runtime-generated data source.",
            "Usually no action required unless the related widget is failing at runtime.",
        )

    if dependency_source == "embedded_config_expression" and not data_source_exists:
        return (
            "unresolved_likely_template_residue",
            "info",
            "Embedded data source reference does not exist in the current ExB dataSources and does not match current web map layers. This often happens when apps are created from reusable templates or copied widgets.",
            "Review only if this widget is visible/active in the current app and users report missing content. Otherwise treat as template residue.",
        )

    if dependency_source == "useDataSources" and not data_source_exists:
        return (
            "unresolved_possible_broken_dependency",
            "warning",
            "Widget actively references a data source that does not exist in the current ExB dataSources and does not match current web map layers.",
            "Review the widget configuration in Experience Builder. Reconnect the widget to the correct map layer or remove the stale widget.",
        )

    if dependency_source == "useDataSources" and data_source_exists:
        return (
            "needs_manual_review_existing_exb_data_source_unmatched_to_webmap",
            "review",
            "The ExB data source exists, but it could not be matched to a current web map layer by ID/title/URL.",
            "Review whether this data source is standalone, generated by a widget, renamed, or linked through a configuration pattern not yet supported by the scanner.",
        )

    return (
        "needs_manual_review_unclassified_reference",
        "review",
        "Reference could not be matched or confidently classified.",
        "Review manually and improve scanner matching rules if this pattern repeats across apps.",
    )


def create_exb_reference_resolution_rows(
    widget_dependency_rows: List[Dict[str, str]],
    layer_rows: List[WebMapLayerRow],
    webmap_item_id: str,
    exb_dependency_file: str,
    webmap_data_source_ids: Set[str],
) -> List[ExBLayerReferenceResolutionRow]:
    if not widget_dependency_rows:
        return []

    layer_lookup = build_layer_lookup(layer_rows)
    rows: List[ExBLayerReferenceResolutionRow] = []

    for dep in widget_dependency_rows:
        exb_ds_id = dep.get("data_source_id", "")
        root_ds = dep.get("root_data_source_id", "")
        main_ds = dep.get("main_data_source_id", "")
        dependency_source = dep.get("dependency_source", "")
        widget_type = dep.get("widget_type", "")

        if not exb_ds_id:
            continue

        # Root web map references should not be treated as unresolved layer issues.
        if is_root_webmap_reference(exb_ds_id, root_ds, main_ds, webmap_data_source_ids):
            status, severity, issue, recommendation = classify_unresolved_reference(dep, webmap_data_source_ids)
            resolved_layer = None
        else:
            resolved_layer, status = resolve_exb_data_source_to_webmap_layer(exb_ds_id, layer_lookup)

            if resolved_layer:
                severity = "ok"
                issue = "ExB data source reference resolved to an active web map layer."
                recommendation = "No action required. Include this layer in health/performance checks."
            else:
                status, severity, issue, recommendation = classify_unresolved_reference(dep, webmap_data_source_ids)

        rows.append(
            ExBLayerReferenceResolutionRow(
                scan_timestamp_utc=now_utc_string(),
                exb_dependency_file=exb_dependency_file,
                webmap_item_id=webmap_item_id,
                widget_id=dep.get("widget_id", ""),
                widget_label=dep.get("widget_label", ""),
                widget_type=widget_type,
                dependency_source=dependency_source,
                exb_data_source_id=exb_ds_id,
                exb_root_data_source_id=root_ds,
                exb_main_data_source_id=main_ds,
                exb_fields=dep.get("fields", ""),
                data_source_exists_in_exb_config=dep.get("data_source_exists", ""),
                resolved_webmap_layer_id=resolved_layer.layer_id if resolved_layer else "",
                resolved_webmap_layer_title=resolved_layer.layer_title if resolved_layer else "",
                resolved_webmap_layer_url=resolved_layer.url if resolved_layer else "",
                resolved_webmap_layer_item_id=resolved_layer.item_id if resolved_layer else "",
                resolution_status=status,
                severity=severity,
                issue_summary=issue,
                recommendation=recommendation,
            )
        )

    return rows


def extract_webmap_data_source_ids(webmap_ref_rows: List[Dict[str, str]]) -> Set[str]:
    ids: Set[str] = set()
    for row in webmap_ref_rows:
        for key in ["data_source_id", "root_data_source_id", "main_data_source_id"]:
            value = (row.get(key) or "").strip()
            if value:
                ids.add(value)
    return ids


# -----------------------------------------------------------------------------
# CLI
# -----------------------------------------------------------------------------

def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Scan web maps referenced by Experience Builder app dependencies."
    )
    parser.add_argument(
        "--webmap-references-csv",
        required=True,
        help="Path to webmap_references_*.csv exported by Script 02.",
    )
    parser.add_argument(
        "--widget-dependencies-csv",
        default=None,
        help="Optional path to widget_data_dependencies_*.csv exported by Script 02 for layer reference resolution.",
    )
    parser.add_argument(
        "--portal",
        default=DEFAULT_PORTAL,
        help="Portal URL. Example: https://www.arcgis.com or https://yourorg.maps.arcgis.com",
    )
    parser.add_argument(
        "--username",
        default=None,
        help="Portal username. If omitted, scan runs anonymously.",
    )
    parser.add_argument(
        "--anonymous",
        action="store_true",
        help="Force anonymous scan.",
    )
    parser.add_argument(
        "--output-prefix",
        default=None,
        help="Optional output filename prefix. Defaults to webmap scan timestamp.",
    )
    return parser.parse_args()


def main() -> int:
    log_path = setup_logging()
    args = parse_args()

    try:
        webmap_refs_path = Path(args.webmap_references_csv)
        widget_deps_path = Path(args.widget_dependencies_csv) if args.widget_dependencies_csv else None

        if not webmap_refs_path.exists():
            raise FileNotFoundError(f"Web map references CSV not found: {webmap_refs_path}")

        timestamp = dt.now().strftime("%Y%m%d_%H%M%S")
        output_prefix = args.output_prefix or f"webmap_scan_{timestamp}"

        logging.info("Starting LHI ExB App Inspector - Script 03")
        logging.info("Web map references CSV: %s", webmap_refs_path)
        if widget_deps_path:
            logging.info("Widget dependencies CSV: %s", widget_deps_path)

        webmap_ref_rows = read_csv_dicts(webmap_refs_path)
        webmap_item_ids = extract_item_ids_from_webmap_references(webmap_ref_rows)
        webmap_data_source_ids = extract_webmap_data_source_ids(webmap_ref_rows)

        if not webmap_item_ids:
            raise RuntimeError("No web map item IDs found in webmap references CSV.")

        widget_dependency_rows = load_optional_widget_dependencies(widget_deps_path)

        gis = connect_to_portal(
            portal_url=args.portal,
            username=args.username,
            anonymous=args.anonymous,
        )

        all_summary_rows: List[WebMapSummaryRow] = []
        all_layer_rows: List[WebMapLayerRow] = []
        all_table_rows: List[WebMapTableRow] = []
        all_resolution_rows: List[ExBLayerReferenceResolutionRow] = []

        for webmap_item_id in webmap_item_ids:
            logging.info("Scanning web map/web scene item: %s", webmap_item_id)
            item = fetch_item(gis, webmap_item_id)
            webmap_data, data_issue = read_item_data(item)

            raw_json_path = save_raw_json(
                prefix=f"webmap_{safe_str(getattr(item, 'title', 'untitled'))}",
                item_id=webmap_item_id,
                data=webmap_data if isinstance(webmap_data, dict) else {},
            )
            logging.info("Raw web map JSON saved to: %s", raw_json_path)

            summary, layer_rows, table_rows = scan_webmap_item(
                portal_url=args.portal,
                item=item,
                webmap_data=webmap_data,
                raw_json_path=raw_json_path,
                data_issue=data_issue,
            )

            all_summary_rows.append(summary)
            all_layer_rows.extend(layer_rows)
            all_table_rows.extend(table_rows)

            if widget_dependency_rows:
                resolution_rows = create_exb_reference_resolution_rows(
                    widget_dependency_rows=widget_dependency_rows,
                    layer_rows=layer_rows,
                    webmap_item_id=webmap_item_id,
                    exb_dependency_file=widget_deps_path.name if widget_deps_path else "",
                    webmap_data_source_ids=webmap_data_source_ids,
                )
                all_resolution_rows.extend(resolution_rows)

        summary_csv = CSV_DIR / f"webmap_summary_{output_prefix}_{timestamp}.csv"
        layers_csv = CSV_DIR / f"webmap_layers_{output_prefix}_{timestamp}.csv"
        tables_csv = CSV_DIR / f"webmap_tables_{output_prefix}_{timestamp}.csv"
        resolution_csv = CSV_DIR / f"exb_layer_reference_resolution_{output_prefix}_{timestamp}.csv"

        write_csv(summary_csv, all_summary_rows)
        write_csv(layers_csv, all_layer_rows)
        write_csv(tables_csv, all_table_rows)
        write_csv(resolution_csv, all_resolution_rows)

        print("\n=== LHI ExB App Inspector: Script 03 Complete ===")
        print(f"Web map references read: {len(webmap_ref_rows)}")
        print(f"Unique web map/web scene items scanned: {len(webmap_item_ids)}")
        print(f"Operational layers extracted: {len(all_layer_rows)}")
        print(f"Tables extracted: {len(all_table_rows)}")
        print(f"ExB layer references classified: {len(all_resolution_rows)}")

        status_counts: Dict[str, int] = {}
        severity_counts: Dict[str, int] = {}
        for row in all_resolution_rows:
            status_counts[row.resolution_status] = status_counts.get(row.resolution_status, 0) + 1
            severity_counts[row.severity] = severity_counts.get(row.severity, 0) + 1

        if all_resolution_rows:
            print("\nResolution status counts:")
            for status, count in sorted(status_counts.items()):
                print(f"  {status}: {count}")

            print("\nSeverity counts:")
            for severity, count in sorted(severity_counts.items()):
                print(f"  {severity}: {count}")

        print("\nOutputs:")
        print(f"Web map summary CSV: {summary_csv}")
        print(f"Web map layers CSV: {layers_csv}")
        print(f"Web map tables CSV: {tables_csv}")
        print(f"ExB layer reference resolution CSV: {resolution_csv}")
        print(f"Log file: {log_path}")

        possible_broken = sum(
            1 for row in all_resolution_rows
            if row.resolution_status == "unresolved_possible_broken_dependency"
        )
        template_residue = sum(
            1 for row in all_resolution_rows
            if row.resolution_status == "unresolved_likely_template_residue"
        )

        if possible_broken:
            print("\nWarning:")
            print(f"{possible_broken} possible broken active widget dependencies were found.")

        if template_residue:
            print("\nObservation:")
            print(f"{template_residue} unresolved references look like likely template residue or copied-widget leftovers.")
            print("These should not be treated as failures unless the related widgets are visible/active and users report issues.")

        return 0

    except Exception as exc:
        logging.exception("Web map scan failed: %s", exc)
        print("\nWeb map scan failed. Check the log file for details:")
        print(log_path)
        print(f"Error: {exc}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
