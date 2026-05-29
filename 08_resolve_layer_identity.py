
"""
LHI ExB App Inspector
Script 08: Layer Identity Resolver v0.8.4

Purpose:
- Read webmap_layers_*.csv from Script 03
- Optionally read layer_health_details_*.csv from Script 04
- Search ArcGIS Online / Portal for matching layer/service items
- Resolve which exact item/service a web map layer appears to use
- Help distinguish layers with the same title, multiple authoritative items, internal services, stale services, and URL-only layers
- Avoid treating same-title matches as source-of-truth for internal URL layers without URL matches

Outputs:
- outputs/csv/layer_identity_resolution_*.csv
- outputs/csv/layer_identity_summary_*.csv

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
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import urlparse

try:
    from arcgis.gis import GIS
except ImportError:
    GIS = None


OUTPUT_ROOT = Path("outputs")
CSV_DIR = OUTPUT_ROOT / "csv"
LOG_DIR = OUTPUT_ROOT / "logs"
DEFAULT_PORTAL = "https://www.arcgis.com"
SCRIPT_VERSION = "0.8.4"


@dataclass
class LayerIdentityResolutionRow:
    scan_timestamp_utc: str
    webmap_item_id: str
    webmap_title: str
    layer_id: str
    layer_title: str
    layer_type_from_webmap: str
    layer_visibility: str
    webmap_layer_item_id: str
    layer_url: str
    service_host: str
    parent_service_url: str
    layer_number: str
    internal_service_detected: bool
    internal_service_reason: str
    health_endpoint_reachable: str
    health_access_mode_used: str
    health_risk_level: str
    item_id_match_found: bool
    exact_url_match_count: int
    parent_url_match_count: int
    same_title_match_count: int
    authoritative_same_title_count: int
    matched_item_id: str
    matched_item_title: str
    matched_item_owner: str
    matched_item_type: str
    matched_item_access: str
    matched_item_url: str
    matched_item_authoritative: str
    matched_item_modified: str
    alternate_candidate_item_ids: str
    alternate_candidate_titles: str
    match_method: str
    match_confidence: str
    identity_status: str
    risk_note: str
    recommendation: str


@dataclass
class LayerIdentitySummaryRow:
    scan_timestamp_utc: str
    input_webmap_layers_csv: str
    input_layer_health_csv: str
    total_layers: int
    layers_with_webmap_item_id: int
    layers_with_url: int
    resolved_by_item_id: int
    resolved_by_exact_url: int
    resolved_by_parent_service_url: int
    ambiguous_same_title: int
    no_portal_item_match: int
    internal_service_count: int
    internal_service_no_portal_match_count: int
    authoritative_same_title_conflict_count: int
    needs_manual_review_count: int
    issue_summary: str


def setup_output_dirs() -> None:
    for folder in [OUTPUT_ROOT, CSV_DIR, LOG_DIR]:
        folder.mkdir(parents=True, exist_ok=True)


def setup_logging() -> Path:
    setup_output_dirs()
    timestamp = dt.now().strftime("%Y%m%d_%H%M%S")
    log_path = LOG_DIR / f"resolve_layer_identity_{timestamp}.log"
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


def safe_str(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, (list, dict)):
        return json.dumps(value, ensure_ascii=False)
    return str(value)


def parse_boolish(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if value is None:
        return False
    return str(value).strip().lower() in {"true", "1", "yes", "y"}


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


def normalize_text(value: str) -> str:
    return re.sub(r"\s+", " ", (value or "").strip()).lower()


def normalize_url(url: str) -> str:
    return (url or "").strip().rstrip("/").lower()


def join_values(values: List[str], max_items: int = 10) -> str:
    cleaned, seen = [], set()
    for value in values:
        v = str(value or "").strip()
        if not v or v in seen:
            continue
        cleaned.append(v)
        seen.add(v)
        if len(cleaned) >= max_items:
            break
    return "; ".join(cleaned)


def item_get(item: Any, key: str, default: Any = "") -> Any:
    try:
        if hasattr(item, key):
            return getattr(item, key)
    except Exception:
        pass
    try:
        return item.get(key, default)
    except Exception:
        return default


def item_to_dict(item: Any) -> Dict[str, Any]:
    try:
        if hasattr(item, "to_dict"):
            return item.to_dict()
    except Exception:
        pass
    return item if isinstance(item, dict) else {}


def format_epoch_ms(value: Any) -> str:
    try:
        if value in [None, ""]:
            return ""
        return dt.fromtimestamp(float(value) / 1000.0, tz=timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
    except Exception:
        return safe_str(value)


def classify_service_host(url: str) -> Tuple[str, bool, str]:
    """
    Classifies internal/dev/test service URL patterns.

    Refinement:
    - Does NOT flag ordinary business words like "Development" or "Development Applications".
    - Flags environment indicators only when they appear as clear host/path tokens,
      such as /dev/, gis-dev, dev-gis, _dev_, test-server, /uat/, /staging/.
    - Strong internal indicators such as appint, gisappint, General_Int, _int, and internal
      are still flagged.
    """
    if not url:
        return "", False, ""

    parsed = urlparse(url)
    host = (parsed.netloc or "").lower()
    path = (parsed.path or "").lower()
    reasons: List[str] = []

    def add(reason: str) -> None:
        if reason not in reasons:
            reasons.append(reason)

    strong_host_patterns = [
        ("localhost", "localhost host"),
        ("127.0.0.1", "loopback host"),
        ("intranet", "intranet host"),
        ("internal", "internal host"),
        ("appint", "internal app server host pattern"),
        ("gisappint", "internal GIS app server host pattern"),
        ("arcgisint", "internal ArcGIS host pattern"),
    ]

    for pattern, reason in strong_host_patterns:
        if pattern in host:
            add(reason)

    strong_path_patterns = [
        ("general_int", "internal General_Int service folder"),
        ("_int", "internal service path pattern"),
        ("/int/", "internal path segment"),
        ("/internal/", "internal service path"),
        ("internal", "internal service path"),
    ]

    for pattern, reason in strong_path_patterns:
        if pattern in path:
            add(reason)

    host_tokens = [token for token in re.split(r"[^a-z0-9]+", host) if token]
    path_tokens = [token for token in re.split(r"[^a-z0-9]+", path) if token]

    environment_tokens = {
        "dev": "development environment token",
        "test": "test environment token",
        "staging": "staging environment token",
        "stage": "staging environment token",
        "uat": "UAT environment token",
        "qa": "QA environment token",
        "sandbox": "sandbox environment token",
    }

    for token in host_tokens + path_tokens:
        reason = environment_tokens.get(token)
        if reason:
            add(reason)

    return host, bool(reasons), "; ".join(sorted(reasons))


def get_parent_service_url_and_layer_number(url: str) -> Tuple[str, str]:
    raw = (url or "").strip().rstrip("/")
    if not raw:
        return "", ""
    match = re.search(r"(.+/(?:FeatureServer|MapServer))/(\\d+)$", raw, flags=re.IGNORECASE)
    if match:
        return match.group(1), match.group(2)
    return raw, ""


def get_layer_url(row: Dict[str, str]) -> str:
    return (row.get("url") or row.get("layer_url") or row.get("service_url") or "").strip()


def get_layer_item_id(row: Dict[str, str]) -> str:
    return (row.get("item_id") or row.get("layer_item_id") or row.get("webmap_layer_item_id") or "").strip()


def connect_to_portal(portal_url: str, username: Optional[str], anonymous: bool) -> Optional[Any]:
    if GIS is None:
        raise ImportError("The arcgis package is not installed. Install it with: pip install arcgis")

    if anonymous:
        logging.info("Running anonymous. Portal item search may be limited.")
        return GIS(portal_url)

    if not username:
        username = input("ArcGIS username: ").strip()

    password = os.getenv("LHI_ARCGIS_PASSWORD")
    if not password:
        password = getpass.getpass(f"Password for {username}: ")

    logging.info("Connecting to portal: %s as %s", portal_url, username)
    return GIS(portal_url, username, password)


def safe_get_item(gis: Any, item_id: str) -> Optional[Any]:
    if not gis or not item_id:
        return None
    try:
        return gis.content.get(item_id)
    except Exception as exc:
        logging.warning("Could not get item %s: %s", item_id, exc)
        return None


def safe_search(gis: Any, query: str, max_items: int = 50) -> List[Any]:
    if not gis or not query:
        return []
    try:
        return gis.content.search(query=query, max_items=max_items)
    except Exception as exc:
        logging.warning("Search failed for query [%s]: %s", query, exc)
        return []


def search_by_title(gis: Any, title: str, max_items: int) -> List[Any]:
    title = (title or "").strip()
    if not title:
        return []
    return safe_search(gis, f'title:"{title}"', max_items=max_items)


def search_by_url_terms(gis: Any, url: str, parent_url: str, max_items: int) -> List[Any]:
    candidates, seen = [], set()
    parsed = urlparse(parent_url or url)
    parts = [p for p in (parsed.path or "").split("/") if p]
    lower_parts = [p.lower() for p in parts]
    service_name = ""

    if "services" in lower_parts:
        try:
            idx = lower_parts.index("services")
            if len(parts) > idx + 2:
                service_name = parts[idx + 2]
            elif len(parts) > idx + 1:
                service_name = parts[idx + 1]
        except Exception:
            service_name = ""

    search_terms = []
    if service_name:
        search_terms.extend([service_name.replace("_", " "), service_name])
    if parsed.netloc:
        search_terms.append(parsed.netloc)

    for term in search_terms:
        for item in safe_search(gis, term, max_items=max_items):
            iid = get_item_id(item)
            if iid and iid not in seen:
                candidates.append(item)
                seen.add(iid)
    return candidates


def get_item_url(item: Any) -> str:
    return safe_str(item_get(item, "url", ""))


def get_item_id(item: Any) -> str:
    return safe_str(item_get(item, "id", ""))


def get_item_title(item: Any) -> str:
    return safe_str(item_get(item, "title", ""))


def get_item_owner(item: Any) -> str:
    return safe_str(item_get(item, "owner", ""))


def get_item_type(item: Any) -> str:
    return safe_str(item_get(item, "type", ""))


def get_item_access(item: Any) -> str:
    return safe_str(item_get(item, "access", ""))


def is_item_authoritative(item: Any) -> str:
    try:
        props = item_get(item, "properties", {})
        if isinstance(props, dict) and str(props.get("isAuthoritative", "")).lower() in {"true", "1", "yes"}:
            return "True"
    except Exception:
        pass
    try:
        if "authoritative" in str(item_get(item, "content_status", "")).lower():
            return "True"
    except Exception:
        pass
    try:
        if "authoritative" in str(item_to_dict(item).get("contentStatus", "")).lower():
            return "True"
    except Exception:
        pass
    return "False"


def item_modified(item: Any) -> str:
    return format_epoch_ms(item_get(item, "modified", ""))


def item_matches_exact_url(item: Any, layer_url: str) -> bool:
    item_url = normalize_url(get_item_url(item))
    layer_url_norm = normalize_url(layer_url)
    return bool(item_url and layer_url_norm and item_url == layer_url_norm)


def item_matches_parent_url(item: Any, parent_url: str) -> bool:
    item_url = normalize_url(get_item_url(item))
    parent_norm = normalize_url(parent_url)
    return bool(item_url and parent_norm and item_url == parent_norm)


def item_url_contains_parent(item: Any, parent_url: str) -> bool:
    item_url = normalize_url(get_item_url(item))
    parent_norm = normalize_url(parent_url)
    return bool(item_url and parent_norm and (parent_norm in item_url or item_url in parent_norm))


def build_health_index(health_rows: List[Dict[str, str]]) -> Dict[str, Dict[str, str]]:
    index: Dict[str, Dict[str, str]] = {}
    for row in health_rows:
        url = normalize_url(row.get("layer_url") or row.get("url") or "")
        if url:
            index[url] = row
    return index


def select_best_candidate(layer_item_id: str, layer_url: str, parent_url: str, layer_title: str,
                          item_id_item: Optional[Any], exact_url_items: List[Any],
                          parent_url_items: List[Any], title_items: List[Any]) -> Tuple[Optional[Any], str, str, str]:
    if item_id_item:
        return item_id_item, "item_id", "high", "resolved_by_item_id"
    if len(exact_url_items) == 1:
        return exact_url_items[0], "exact_url", "high", "resolved_by_exact_url"
    if len(exact_url_items) > 1:
        return exact_url_items[0], "exact_url_multiple", "medium", "multiple_exact_url_matches"
    if len(parent_url_items) == 1:
        return parent_url_items[0], "parent_service_url", "medium", "resolved_by_parent_service_url"
    if len(parent_url_items) > 1:
        return parent_url_items[0], "parent_service_url_multiple", "medium", "multiple_parent_service_matches"

    exact_title = [i for i in title_items if normalize_text(get_item_title(i)) == normalize_text(layer_title)]
    if len(exact_title) == 1:
        return exact_title[0], "exact_title", "low", "resolved_by_title_only"
    if len(exact_title) > 1:
        authoritative = [i for i in exact_title if is_item_authoritative(i) == "True"]
        if len(authoritative) == 1:
            return authoritative[0], "title_authoritative_single", "low", "resolved_by_title_authoritative_hint"
        return exact_title[0], "title_multiple", "low", "ambiguous_same_title_matches"

    return None, "none", "none", "no_portal_item_match"


def resolve_one_layer(gis: Any, layer_row: Dict[str, str], health_index: Dict[str, Dict[str, str]], max_search_results: int) -> LayerIdentityResolutionRow:
    layer_url = get_layer_url(layer_row)
    parent_url, layer_number = get_parent_service_url_and_layer_number(layer_url)
    layer_item_id = get_layer_item_id(layer_row)
    layer_title = layer_row.get("layer_title") or layer_row.get("title") or ""
    service_host, internal_detected, internal_reason = classify_service_host(layer_url)
    health = health_index.get(normalize_url(layer_url), {})

    if health:
        if parse_boolish(health.get("internal_service_detected")):
            internal_detected = True
            internal_reason = health.get("internal_service_reason") or internal_reason
        service_host = health.get("service_host") or service_host

    item_id_item = safe_get_item(gis, layer_item_id) if layer_item_id else None
    title_items = search_by_title(gis, layer_title, max_search_results) if layer_title else []
    url_search_items = search_by_url_terms(gis, layer_url, parent_url, max_search_results)

    all_candidates, seen_ids = [], set()
    for item in ([item_id_item] if item_id_item else []) + title_items + url_search_items:
        iid = get_item_id(item)
        if iid and iid not in seen_ids:
            all_candidates.append(item)
            seen_ids.add(iid)

    exact_url_items = [i for i in all_candidates if item_matches_exact_url(i, layer_url)]
    parent_url_items = [i for i in all_candidates if item_matches_parent_url(i, parent_url) or item_url_contains_parent(i, parent_url)]
    exact_title_items = [i for i in title_items if normalize_text(get_item_title(i)) == normalize_text(layer_title)]
    authoritative_same_title = [i for i in exact_title_items if is_item_authoritative(i) == "True"]

    best, method, confidence, identity_status = select_best_candidate(
        layer_item_id, layer_url, parent_url, layer_title,
        item_id_item, exact_url_items, parent_url_items, title_items
    )

    # IMPORTANT:
    # If the layer is an internal/dev/test URL and there is no exact URL or parent URL match,
    # do NOT treat same-title portal items as the matched source item. In many orgs,
    # several authoritative items can share a title like "Development Applications".
    # In that case, the web map layer URL is the source of truth.
    title_only_internal_ambiguous = (
        internal_detected
        and len(exact_url_items) == 0
        and len(parent_url_items) == 0
        and len(exact_title_items) > 0
        and method.startswith("title")
    )

    if title_only_internal_ambiguous:
        best = None
        matched_id = ""
        method = "title_candidates_only_internal_url"
        confidence = "low"

        if len(authoritative_same_title) > 1:
            identity_status = "ambiguous_authoritative_title_candidates_internal_url"
        else:
            identity_status = "title_candidates_only_internal_url"

    elif internal_detected and not best:
        matched_id = ""
        identity_status = "internal_service_no_portal_item"
        method = "internal_url_no_portal_match"
        confidence = "medium"

    else:
        if len(authoritative_same_title) > 1 and method.startswith("title"):
            identity_status = "ambiguous_authoritative_matches"
            confidence = "low"

        matched_id = get_item_id(best) if best else ""

    # Final safety override:
    # For internal/dev/test URL layers with no exact URL or parent-service match,
    # same-title candidates must remain candidates only. They are not source-of-truth.
    if (
        internal_detected
        and len(exact_url_items) == 0
        and len(parent_url_items) == 0
        and len(exact_title_items) > 0
    ):
        best = None
        matched_id = ""
        method = "title_candidates_only_internal_url"
        confidence = "low"
        if len(authoritative_same_title) > 1:
            identity_status = "ambiguous_authoritative_title_candidates_internal_url"
        else:
            identity_status = "title_candidates_only_internal_url"

    alt_candidates = [i for i in all_candidates if get_item_id(i) != matched_id]

    risk_note, recommendation = build_recommendation(identity_status, internal_detected)

    return LayerIdentityResolutionRow(
        scan_timestamp_utc=now_utc_string(),
        webmap_item_id=layer_row.get("webmap_item_id", ""),
        webmap_title=layer_row.get("webmap_title", ""),
        layer_id=layer_row.get("layer_id", ""),
        layer_title=layer_title,
        layer_type_from_webmap=layer_row.get("layer_type", ""),
        layer_visibility=layer_row.get("visibility", ""),
        webmap_layer_item_id=layer_item_id,
        layer_url=layer_url,
        service_host=service_host,
        parent_service_url=parent_url,
        layer_number=layer_number,
        internal_service_detected=internal_detected,
        internal_service_reason=internal_reason,
        health_endpoint_reachable=health.get("endpoint_reachable", ""),
        health_access_mode_used=health.get("access_mode_used", ""),
        health_risk_level=health.get("risk_level", ""),
        item_id_match_found=bool(item_id_item),
        exact_url_match_count=len(exact_url_items),
        parent_url_match_count=len(parent_url_items),
        same_title_match_count=len(exact_title_items),
        authoritative_same_title_count=len(authoritative_same_title),
        matched_item_id=matched_id,
        matched_item_title=get_item_title(best) if best else "",
        matched_item_owner=get_item_owner(best) if best else "",
        matched_item_type=get_item_type(best) if best else "",
        matched_item_access=get_item_access(best) if best else "",
        matched_item_url=get_item_url(best) if best else "",
        matched_item_authoritative=is_item_authoritative(best) if best else "",
        matched_item_modified=item_modified(best) if best else "",
        alternate_candidate_item_ids=join_values([get_item_id(i) for i in alt_candidates], 20),
        alternate_candidate_titles=join_values([get_item_title(i) for i in alt_candidates], 20),
        match_method=method,
        match_confidence=confidence,
        identity_status=identity_status,
        risk_note=risk_note,
        recommendation=recommendation,
    )


def build_recommendation(identity_status: str, internal_detected: bool) -> Tuple[str, str]:
    if identity_status in {"resolved_by_item_id", "resolved_by_exact_url"}:
        return "Layer identity is strongly resolved.", "No identity action required. Use matched item ID/URL as source of truth."
    if identity_status == "resolved_by_parent_service_url":
        return "Layer is resolved to a parent service item, but sublayer identity may still need review.", "Confirm the sublayer number and service item are the intended source."
    if identity_status == "internal_service_no_portal_item":
        return "Layer uses an internal-looking service URL and no matching portal item was found.", "Validate the internal service URL directly. If the app is public/broadly shared, consider replacing it with a hosted/public view or restrict the app audience."
    if identity_status in {"ambiguous_authoritative_title_candidates_internal_url", "title_candidates_only_internal_url"}:
        return (
            "Internal URL layer has same-title portal candidates, but no exact URL or parent service match.",
            "Do not treat same-title candidates as the source item. Use the web map layer URL as the source of truth and manually validate any candidate items by URL, owner, and service lineage."
        )
    if identity_status == "ambiguous_authoritative_matches":
        return "Multiple authoritative items with the same title were found.", "Do not rely on title. Validate by service URL, item ID, owner, and the web map layer URL."
    if "ambiguous" in identity_status or "multiple" in identity_status:
        return "Multiple possible matching items were found.", "Review candidate items and confirm the correct source using URL and owner."
    if identity_status == "no_portal_item_match":
        if internal_detected:
            return "No matching portal item was found for an internal-looking service.", "Use the web map layer URL as the source of truth and confirm whether this is expected."
        return "No matching portal item was found.", "Use the web map layer URL as the source of truth. Confirm whether this is a URL-only ArcGIS Server layer."
    return "Layer identity needs review.", "Review item ID, URL, owner, authoritative status, and same-title candidates."


def build_summary(webmap_layers_csv: str, layer_health_csv: str, rows: List[LayerIdentityResolutionRow]) -> LayerIdentitySummaryRow:
    def count_where(predicate) -> int:
        return sum(1 for row in rows if predicate(row))

    no_match_count = count_where(lambda r: r.identity_status in {"no_portal_item_match", "internal_service_no_portal_item"})
    internal_count = count_where(lambda r: r.internal_service_detected)
    ambiguous_auth = count_where(lambda r: r.identity_status == "ambiguous_authoritative_matches")
    internal_title_candidates = count_where(lambda r: r.identity_status in {"ambiguous_authoritative_title_candidates_internal_url", "title_candidates_only_internal_url"})
    manual_review = count_where(lambda r: r.match_confidence in {"low", "none"} or "ambiguous" in r.identity_status or "title_candidates_only" in r.identity_status)

    issues = []
    if no_match_count:
        issues.append(f"{no_match_count} layer(s) did not resolve to a clear portal item.")
    if internal_count:
        issues.append(f"{internal_count} layer(s) appear to reference internal/dev/test service endpoints.")
    if ambiguous_auth:
        issues.append(f"{ambiguous_auth} layer(s) have multiple same-title authoritative candidates.")
    if internal_title_candidates:
        issues.append(f"{internal_title_candidates} internal URL layer(s) have same-title portal candidates but no URL match.")
    if manual_review:
        issues.append(f"{manual_review} layer(s) need manual identity review.")
    if not issues:
        issues.append("All layers resolved clearly enough for identity review.")

    return LayerIdentitySummaryRow(
        scan_timestamp_utc=now_utc_string(),
        input_webmap_layers_csv=webmap_layers_csv,
        input_layer_health_csv=layer_health_csv,
        total_layers=len(rows),
        layers_with_webmap_item_id=count_where(lambda r: bool(r.webmap_layer_item_id)),
        layers_with_url=count_where(lambda r: bool(r.layer_url)),
        resolved_by_item_id=count_where(lambda r: r.identity_status == "resolved_by_item_id"),
        resolved_by_exact_url=count_where(lambda r: r.identity_status == "resolved_by_exact_url"),
        resolved_by_parent_service_url=count_where(lambda r: r.identity_status == "resolved_by_parent_service_url"),
        ambiguous_same_title=count_where(lambda r: "ambiguous" in r.identity_status),
        no_portal_item_match=no_match_count,
        internal_service_count=internal_count,
        internal_service_no_portal_match_count=count_where(lambda r: r.identity_status == "internal_service_no_portal_item"),
        authoritative_same_title_conflict_count=ambiguous_auth,
        needs_manual_review_count=manual_review,
        issue_summary=" | ".join(issues),
    )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Resolve source identity for web map layers used by Experience Builder apps.")
    parser.add_argument("--webmap-layers-csv", required=True, help="Path to webmap_layers_*.csv from Script 03.")
    parser.add_argument("--layer-health-details-csv", required=False, default=None, help="Optional path to layer_health_details_*.csv from Script 04.")
    parser.add_argument("--portal", default=DEFAULT_PORTAL, help="Portal URL.")
    parser.add_argument("--username", default=None, help="Portal username. If omitted and --anonymous is not used, the script asks once.")
    parser.add_argument("--anonymous", action="store_true", help="Run anonymously. Item search may be limited.")
    parser.add_argument("--max-search-results", type=int, default=50, help="Maximum items to retrieve per title/URL search. Default: 50.")
    parser.add_argument("--output-prefix", default=None, help="Optional output filename prefix.")
    return parser.parse_args()


def main() -> int:
    log_path = setup_logging()
    args = parse_args()

    try:
        layers_path = Path(args.webmap_layers_csv)
        if not layers_path.exists():
            raise FileNotFoundError(f"Web map layers CSV not found: {layers_path}")

        health_rows: List[Dict[str, str]] = []
        health_path_text = ""
        if args.layer_health_details_csv:
            health_path = Path(args.layer_health_details_csv)
            if not health_path.exists():
                raise FileNotFoundError(f"Layer health details CSV not found: {health_path}")
            health_rows = read_csv_dicts(health_path)
            health_path_text = health_path.name

        layer_rows = read_csv_dicts(layers_path)
        health_index = build_health_index(health_rows)

        timestamp = dt.now().strftime("%Y%m%d_%H%M%S")
        output_prefix = args.output_prefix or f"layer_identity_{timestamp}"

        logging.info("Starting LHI ExB App Inspector - Script 08 v%s", SCRIPT_VERSION)
        logging.info("Web map layers CSV: %s", layers_path)
        logging.info("Layer health details CSV: %s", args.layer_health_details_csv or "")
        logging.info("Layer rows: %s", len(layer_rows))

        gis = connect_to_portal(args.portal, args.username, args.anonymous)

        resolution_rows: List[LayerIdentityResolutionRow] = []
        for index, row in enumerate(layer_rows, start=1):
            title = row.get("layer_title", "")
            url = get_layer_url(row)
            logging.info("Resolving layer %s/%s: %s | %s", index, len(layer_rows), title, url)
            resolution_rows.append(resolve_one_layer(gis, row, health_index, args.max_search_results))

        summary = build_summary(layers_path.name, health_path_text, resolution_rows)

        summary_csv = CSV_DIR / f"layer_identity_summary_{output_prefix}_{timestamp}.csv"
        details_csv = CSV_DIR / f"layer_identity_resolution_{output_prefix}_{timestamp}.csv"

        write_csv(summary_csv, [summary])
        write_csv(details_csv, resolution_rows)

        print(f"\n=== LHI ExB App Inspector: Script 08 v{SCRIPT_VERSION} Complete ===")
        print(f"Layers resolved: {summary.total_layers}")
        print(f"Resolved by item ID: {summary.resolved_by_item_id}")
        print(f"Resolved by exact URL: {summary.resolved_by_exact_url}")
        print(f"Resolved by parent service URL: {summary.resolved_by_parent_service_url}")
        print(f"No portal item match: {summary.no_portal_item_match}")
        print(f"Internal service layers: {summary.internal_service_count}")
        print(f"Ambiguous same-title layers: {summary.ambiguous_same_title}")
        print(f"Needs manual review: {summary.needs_manual_review_count}")
        print("\nOutputs:")
        print(f"Layer identity summary CSV: {summary_csv}")
        print(f"Layer identity resolution CSV: {details_csv}")
        print(f"Log file: {log_path}")
        print("\nSummary:")
        print(summary.issue_summary)

        return 0

    except Exception as exc:
        logging.exception("Layer identity resolution failed: %s", exc)
        print("\nLayer identity resolution failed. Check the log file for details:")
        print(log_path)
        print(f"Error: {exc}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
