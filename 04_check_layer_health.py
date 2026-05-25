"""
LHI ExB App Inspector
Script 04: Layer Health Checker v0.8.2

Purpose:
- Read webmap_layers_*.csv from Script 03
- Test each operational layer/table REST endpoint
- Use public/anonymous access first by default
- If anonymous fails, retry with authenticated token when username is supplied
- Avoid false critical failures caused by sending an AGOL token to a non-federated/public ArcGIS Server service
- Check service metadata availability
- Check query support
- Run lightweight count and sample queries
- Measure response times
- Detect token/security/access errors
- Classify internal/service-host patterns such as internal ArcGIS Server endpoints
- Capture geometry type, fields count, object ID field, max record count, capabilities
- Produce layer health CSVs for reporting and risk scoring

Author: Lazy Hat Innovations
"""

from __future__ import annotations

import argparse
import csv
import getpass
import json
import logging
import os
import sys
import time
from dataclasses import dataclass, asdict
from datetime import datetime as dt, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import urlparse

import requests

try:
    from arcgis.gis import GIS
except ImportError:
    GIS = None


# -----------------------------------------------------------------------------
# Defaults
# -----------------------------------------------------------------------------

OUTPUT_ROOT = Path("outputs")
CSV_DIR = OUTPUT_ROOT / "csv"
LOG_DIR = OUTPUT_ROOT / "logs"
DEFAULT_PORTAL = "https://www.arcgis.com"
DEFAULT_TIMEOUT = 30


# -----------------------------------------------------------------------------
# Data models
# -----------------------------------------------------------------------------

@dataclass
class LayerHealthDetailRow:
    scan_timestamp_utc: str
    webmap_item_id: str
    webmap_title: str
    layer_id: str
    layer_title: str
    layer_type_from_webmap: str
    layer_url: str
    service_host: str
    service_path: str
    internal_service_detected: bool
    internal_service_reason: str
    layer_item_id: str
    visibility: str
    has_definition_expression_from_webmap: bool
    definition_expression_from_webmap: str
    access_mode_used: str
    anonymous_metadata_success: bool
    authenticated_metadata_attempted: bool
    authenticated_metadata_success: bool
    endpoint_reachable: bool
    metadata_status_code: str
    metadata_response_time_sec: str
    metadata_error: str
    token_required_or_access_denied: bool
    invalid_token_seen: bool
    service_current_version: str
    service_type: str
    geometry_type: str
    object_id_field: str
    display_field: str
    fields_count: str
    max_record_count: str
    capabilities: str
    supports_query: bool
    supports_statistics: bool
    supports_advanced_queries: bool
    supports_pagination: bool
    supports_order_by: bool
    supports_distinct: bool
    editing_enabled: bool
    attachments_enabled: str
    has_z: str
    has_m: str
    extent_available: bool
    count_query_attempted: bool
    count_query_success: bool
    count_query_response_time_sec: str
    feature_count: str
    count_query_error: str
    sample_query_attempted: bool
    sample_query_success: bool
    sample_query_response_time_sec: str
    sample_feature_count_returned: str
    sample_query_error: str
    health_status: str
    risk_level: str
    risk_score: int
    issue_summary: str
    recommendation: str


@dataclass
class LayerHealthSummaryRow:
    scan_timestamp_utc: str
    input_file: str
    access_strategy: str
    total_layer_rows_read: int
    unique_layer_urls_checked: int
    internal_service_count: int
    internal_service_unreachable_count: int
    reachable_count: int
    unreachable_count: int
    public_reachable_count: int
    auth_required_and_auth_success_count: int
    auth_required_but_not_tested_count: int
    token_or_access_denied_count: int
    invalid_token_seen_count: int
    query_supported_count: int
    count_query_success_count: int
    sample_query_success_count: int
    low_risk_count: int
    medium_risk_count: int
    high_risk_count: int
    critical_risk_count: int
    average_metadata_response_time_sec: str
    average_count_query_response_time_sec: str
    slow_layer_count: int
    issue_summary: str


# -----------------------------------------------------------------------------
# Setup and utilities
# -----------------------------------------------------------------------------

def setup_output_dirs() -> None:
    for folder in [OUTPUT_ROOT, CSV_DIR, LOG_DIR]:
        folder.mkdir(parents=True, exist_ok=True)


def setup_logging() -> Path:
    setup_output_dirs()
    timestamp = dt.now().strftime("%Y%m%d_%H%M%S")
    log_path = LOG_DIR / f"check_layer_health_{timestamp}.log"

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
    if isinstance(value, (dict, list)):
        return json.dumps(value, ensure_ascii=False)
    return str(value)


def parse_boolish(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if value is None:
        return False
    text = str(value).strip().lower()
    return text in {"true", "1", "yes", "y"}


def parse_float(value: Any) -> Optional[float]:
    try:
        if value in [None, ""]:
            return None
        return float(value)
    except Exception:
        return None


def format_seconds(value: Optional[float]) -> str:
    if value is None:
        return ""
    return f"{value:.3f}"


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


def is_valid_url(url: str) -> bool:
    if not url:
        return False
    parsed = urlparse(url)
    return parsed.scheme in {"http", "https"} and bool(parsed.netloc)


def classify_service_host(url: str) -> Tuple[str, str, bool, str]:
    """
    Classifies service host/path patterns that often indicate internal,
    development, test, or non-public ArcGIS Server endpoints.

    This does not prove a service is inaccessible; it is a governance signal
    used by later reporting logic.
    """
    if not url:
        return "", "", False, ""

    parsed = urlparse(url)
    host = (parsed.netloc or "").lower()
    path = (parsed.path or "").lower()

    reasons: List[str] = []

    host_patterns = [
        ("localhost", "localhost host"),
        ("127.0.0.1", "loopback host"),
        ("intranet", "intranet host"),
        ("internal", "internal host"),
        ("appint", "internal app server host pattern"),
        ("gisappint", "internal GIS app server host pattern"),
        ("arcgisint", "internal ArcGIS host pattern"),
        ("dev", "development host pattern"),
        ("test", "test host pattern"),
        ("staging", "staging host pattern"),
        ("uat", "UAT host pattern"),
    ]

    path_patterns = [
        ("_int", "internal service path pattern"),
        ("/int/", "internal path segment"),
        ("general_int", "internal General_Int service folder"),
        ("internal", "internal service path"),
        ("dev", "development service path"),
        ("test", "test service path"),
        ("staging", "staging service path"),
        ("uat", "UAT service path"),
    ]

    for pattern, reason in host_patterns:
        if pattern in host:
            reasons.append(reason)

    for pattern, reason in path_patterns:
        if pattern in path:
            reasons.append(reason)

    internal = bool(reasons)
    return host, parsed.path or "", internal, "; ".join(sorted(set(reasons)))


def dedupe_layer_rows(rows: List[Dict[str, str]]) -> List[Dict[str, str]]:
    output: List[Dict[str, str]] = []
    seen = set()

    for row in rows:
        url = (row.get("url") or row.get("layer_url") or "").strip()
        key = url.lower() if url else "|".join([
            row.get("webmap_item_id", ""),
            row.get("layer_id", ""),
            row.get("layer_title", ""),
        ]).lower()

        if key in seen:
            continue
        seen.add(key)
        output.append(row)

    return output


# -----------------------------------------------------------------------------
# Auth and REST helpers
# -----------------------------------------------------------------------------

def get_portal_token(portal_url: str, username: Optional[str], anonymous: bool) -> Optional[str]:
    if anonymous:
        logging.info("Anonymous mode forced. No token will be used.")
        return None

    if not username:
        logging.info("No username supplied. Authenticated fallback will not be available.")
        return None

    if GIS is None:
        raise ImportError("The arcgis package is not installed. Install it with: pip install arcgis")

    password = os.getenv("LHI_ARCGIS_PASSWORD")
    if not password:
        password = getpass.getpass(f"Password for {username}: ")

    logging.info("Connecting as %s to portal for authenticated fallback: %s", username, portal_url)
    gis = GIS(portal_url, username, password)

    try:
        return gis._con.token
    except Exception:
        return None


def request_json(url: str, params: Dict[str, Any], timeout: int = DEFAULT_TIMEOUT) -> Tuple[bool, str, Optional[float], Optional[Dict[str, Any]], str]:
    start = time.perf_counter()
    try:
        response = requests.get(url, params=params, timeout=timeout)
        elapsed = time.perf_counter() - start
        status_code = str(response.status_code)

        try:
            data = response.json()
        except Exception:
            return False, status_code, elapsed, None, "Response was not valid JSON."

        if response.status_code >= 400:
            return False, status_code, elapsed, data, f"HTTP {response.status_code} error."

        if isinstance(data, dict) and "error" in data:
            err = data.get("error") or {}
            message = safe_str(err.get("message") if isinstance(err, dict) else err)
            details = err.get("details") if isinstance(err, dict) else None
            details_text = safe_str(details)
            return False, status_code, elapsed, data, f"ArcGIS REST error: {message} {details_text}".strip()

        return True, status_code, elapsed, data, ""

    except requests.exceptions.Timeout:
        elapsed = time.perf_counter() - start
        return False, "timeout", elapsed, None, f"Request timed out after {timeout} seconds."
    except requests.exceptions.SSLError as exc:
        elapsed = time.perf_counter() - start
        return False, "ssl_error", elapsed, None, f"SSL error: {exc}"
    except requests.exceptions.RequestException as exc:
        elapsed = time.perf_counter() - start
        return False, "request_error", elapsed, None, f"Request error: {exc}"
    except Exception as exc:
        elapsed = time.perf_counter() - start
        return False, "unknown_error", elapsed, None, f"Unexpected error: {exc}"


def is_access_denied_error(error_text: str, data: Optional[Dict[str, Any]]) -> bool:
    text = (error_text or "").lower()
    if "token" in text or "permission" in text or "access" in text or "not authorized" in text:
        return True

    if isinstance(data, dict) and "error" in data:
        err = data.get("error")
        if isinstance(err, dict):
            code = safe_str(err.get("code"))
            message = safe_str(err.get("message")).lower()
            if code in {"498", "499", "403"}:
                return True
            if "token" in message or "not authorized" in message or "access" in message:
                return True

    return False


def is_invalid_token_error(error_text: str, data: Optional[Dict[str, Any]]) -> bool:
    text = (error_text or "").lower()
    if "invalid token" in text:
        return True

    if isinstance(data, dict) and "error" in data:
        err = data.get("error")
        if isinstance(err, dict):
            code = safe_str(err.get("code"))
            message = safe_str(err.get("message")).lower()
            if code == "498" or "invalid token" in message:
                return True

    return False


def metadata_request(layer_url: str, token: Optional[str], timeout: int) -> Tuple[bool, str, Optional[float], Optional[Dict[str, Any]], str]:
    params: Dict[str, Any] = {"f": "json"}
    if token:
        params["token"] = token
    return request_json(layer_url, params=params, timeout=timeout)


def get_metadata_with_access_strategy(
    layer_url: str,
    token: Optional[str],
    timeout: int,
    auth_first: bool,
) -> Dict[str, Any]:
    """
    Returns a dictionary containing the winning metadata response plus access diagnostics.

    Default strategy is anonymous-first:
    1. Try anonymous metadata request.
    2. If anonymous succeeds, use it.
    3. If anonymous fails and token exists, try authenticated.
    4. If authenticated fails with Invalid Token and anonymous was not already tried first, retry anonymous.
    """
    result: Dict[str, Any] = {
        "access_mode_used": "none",
        "anonymous_success": False,
        "authenticated_attempted": False,
        "authenticated_success": False,
        "invalid_token_seen": False,
        "token_or_access_denied": False,
        "success": False,
        "status_code": "",
        "elapsed": None,
        "data": None,
        "error": "",
    }

    def apply_response(mode: str, success: bool, status_code: str, elapsed: Optional[float], data: Optional[Dict[str, Any]], error: str) -> None:
        result["access_mode_used"] = mode
        result["success"] = success
        result["status_code"] = status_code
        result["elapsed"] = elapsed
        result["data"] = data
        result["error"] = error
        if is_access_denied_error(error, data):
            result["token_or_access_denied"] = True
        if is_invalid_token_error(error, data):
            result["invalid_token_seen"] = True

    # Auth-first is optional for internal app testing. Default is anonymous-first.
    if auth_first and token:
        result["authenticated_attempted"] = True
        success, status, elapsed, data, error = metadata_request(layer_url, token=token, timeout=timeout)
        if success:
            result["authenticated_success"] = True
            apply_response("authenticated", success, status, elapsed, data, error)
            return result

        if is_invalid_token_error(error, data):
            result["invalid_token_seen"] = True
            logging.info("Authenticated request returned invalid token. Retrying anonymously: %s", layer_url)
            anon_success, anon_status, anon_elapsed, anon_data, anon_error = metadata_request(layer_url, token=None, timeout=timeout)
            result["anonymous_success"] = anon_success
            if anon_success:
                apply_response("anonymous_after_invalid_token", anon_success, anon_status, anon_elapsed, anon_data, anon_error)
                return result

        apply_response("authenticated", success, status, elapsed, data, error)
        return result

    # Default: anonymous first.
    anon_success, anon_status, anon_elapsed, anon_data, anon_error = metadata_request(layer_url, token=None, timeout=timeout)
    result["anonymous_success"] = anon_success

    if anon_success:
        apply_response("anonymous", anon_success, anon_status, anon_elapsed, anon_data, anon_error)
        return result

    # Anonymous failed. Try authenticated only if we have a token.
    if token:
        result["authenticated_attempted"] = True
        auth_success, auth_status, auth_elapsed, auth_data, auth_error = metadata_request(layer_url, token=token, timeout=timeout)
        result["authenticated_success"] = auth_success

        if auth_success:
            apply_response("authenticated_after_anonymous_failed", auth_success, auth_status, auth_elapsed, auth_data, auth_error)
            return result

        if is_invalid_token_error(auth_error, auth_data):
            result["invalid_token_seen"] = True

        apply_response("authenticated_after_anonymous_failed", auth_success, auth_status, auth_elapsed, auth_data, auth_error)
        return result

    apply_response("anonymous", anon_success, anon_status, anon_elapsed, anon_data, anon_error)
    return result


# -----------------------------------------------------------------------------
# Layer metadata extraction
# -----------------------------------------------------------------------------

def get_advanced_query_capability(metadata: Dict[str, Any], key: str) -> bool:
    advanced = metadata.get("advancedQueryCapabilities")
    if isinstance(advanced, dict):
        return bool(advanced.get(key))
    return False


def supports_query(metadata: Dict[str, Any]) -> bool:
    capabilities = safe_str(metadata.get("capabilities")).lower()
    if "query" in capabilities:
        return True
    if metadata.get("supportsQuery") is True:
        return True
    return False


def editing_enabled(metadata: Dict[str, Any]) -> bool:
    capabilities = safe_str(metadata.get("capabilities")).lower()
    edit_terms = ["create", "update", "delete", "editing", "edit"]
    return any(term in capabilities for term in edit_terms)


def get_object_id_field(metadata: Dict[str, Any]) -> str:
    object_id_field = metadata.get("objectIdField") or metadata.get("objectIdFieldName")
    if object_id_field:
        return safe_str(object_id_field)

    fields = metadata.get("fields")
    if isinstance(fields, list):
        for field in fields:
            if isinstance(field, dict) and field.get("type") == "esriFieldTypeOID":
                return safe_str(field.get("name"))

    return ""


def fields_count(metadata: Dict[str, Any]) -> str:
    fields = metadata.get("fields")
    if isinstance(fields, list):
        return str(len(fields))
    return ""


def extent_available(metadata: Dict[str, Any]) -> bool:
    extent = metadata.get("extent")
    return isinstance(extent, dict) and bool(extent)


# -----------------------------------------------------------------------------
# Query checks
# -----------------------------------------------------------------------------

def build_query_params(token: Optional[str], where: str, return_count_only: bool = False, result_record_count: Optional[int] = None) -> Dict[str, Any]:
    params: Dict[str, Any] = {
        "f": "json",
        "where": where or "1=1",
        "returnGeometry": "false",
        "outFields": "*",
    }

    if return_count_only:
        params["returnCountOnly"] = "true"
        params.pop("outFields", None)
    else:
        params["returnCountOnly"] = "false"
        if result_record_count is not None:
            params["resultRecordCount"] = result_record_count

    if token:
        params["token"] = token

    return params


def run_count_query(layer_url: str, token: Optional[str], where: str, timeout: int) -> Tuple[bool, Optional[float], str, str]:
    query_url = layer_url.rstrip("/") + "/query"
    params = build_query_params(token=token, where=where, return_count_only=True)
    success, status_code, elapsed, data, error = request_json(query_url, params=params, timeout=timeout)

    if not success:
        return False, elapsed, "", error

    count = ""
    if isinstance(data, dict):
        count = safe_str(data.get("count"))
        if count == "":
            return False, elapsed, "", "Count query succeeded but no count value was returned."

    return True, elapsed, count, ""


def run_sample_query(layer_url: str, token: Optional[str], where: str, timeout: int, sample_size: int = 5) -> Tuple[bool, Optional[float], str, str]:
    query_url = layer_url.rstrip("/") + "/query"
    params = build_query_params(token=token, where=where, return_count_only=False, result_record_count=sample_size)
    success, status_code, elapsed, data, error = request_json(query_url, params=params, timeout=timeout)

    if not success:
        return False, elapsed, "", error

    if isinstance(data, dict):
        features = data.get("features")
        if isinstance(features, list):
            return True, elapsed, str(len(features)), ""
        return False, elapsed, "", "Sample query succeeded but no features array was returned."

    return False, elapsed, "", "Sample query returned unexpected JSON structure."


# -----------------------------------------------------------------------------
# Health scoring
# -----------------------------------------------------------------------------

def evaluate_health(
    endpoint_reachable: bool,
    token_denied: bool,
    invalid_token_seen: bool,
    metadata_error: str,
    metadata_time: Optional[float],
    query_supported: bool,
    count_attempted: bool,
    count_success: bool,
    count_error: str,
    sample_attempted: bool,
    sample_success: bool,
    sample_error: str,
    access_mode_used: str,
    internal_service_detected: bool = False,
    internal_service_reason: str = "",
) -> Tuple[str, str, int, str, str]:
    issues: List[str] = []
    recommendations: List[str] = []
    score = 0

    if internal_service_detected:
        issues.append(f"Layer URL appears to reference an internal/dev/test service endpoint ({internal_service_reason}).")
        recommendations.append("Confirm whether this internal service is appropriate for the app audience. Use a public/hosted view for public-facing apps or restrict the app/web map to the intended internal audience.")
        if not endpoint_reachable:
            score += 20

    if invalid_token_seen and endpoint_reachable:
        issues.append("Invalid token was seen during fallback testing, but the layer was reachable through another access mode.")
        recommendations.append("No immediate layer fix required; report should note mixed AGOL/Enterprise token behavior.")

    if token_denied and not endpoint_reachable:
        score += 40
        issues.append("Endpoint appears to require a token or access is denied.")
        recommendations.append("Verify layer sharing matches the Experience Builder app audience and test with a user who should have access.")

    if not endpoint_reachable:
        score += 50
        issues.append("Layer metadata endpoint is not reachable or returned an error.")
        if metadata_error:
            issues.append(metadata_error)
        recommendations.append("Check whether the REST service URL is valid and accessible to intended users.")

    if metadata_time is not None:
        if metadata_time > 8:
            score += 25
            issues.append(f"Metadata response is very slow ({metadata_time:.2f}s).")
            recommendations.append("Investigate service performance, network latency, or service load.")
        elif metadata_time > 3:
            score += 10
            issues.append(f"Metadata response is slow ({metadata_time:.2f}s).")
            recommendations.append("Monitor service performance; consider simplifying layers or service configuration if users report slow loading.")

    if endpoint_reachable and not query_supported:
        score += 20
        issues.append("Layer does not advertise Query capability.")
        recommendations.append("Confirm whether this layer is intended for widgets requiring query/list/table/filter behavior.")

    if count_attempted and not count_success:
        score += 20
        issues.append("Count query failed.")
        if count_error:
            issues.append(count_error)
        recommendations.append("Check definition expressions, query capability, permissions, and service health.")

    if sample_attempted and not sample_success:
        score += 10
        issues.append("Sample feature query failed.")
        if sample_error:
            issues.append(sample_error)
        recommendations.append("Review whether the layer supports feature queries and whether field/geometry restrictions apply.")

    if score >= 70:
        risk = "critical"
        status = "fail"
    elif score >= 40:
        risk = "high"
        status = "warning"
    elif score >= 15:
        risk = "medium"
        status = "review"
    else:
        risk = "low"
        status = "ok"

    if not issues:
        issues.append(f"Layer endpoint and lightweight queries appear healthy using {access_mode_used} access.")
        recommendations.append("No immediate action required.")

    return status, risk, score, " | ".join(issues), " | ".join(recommendations)


# -----------------------------------------------------------------------------
# Main scan function
# -----------------------------------------------------------------------------

def determine_query_where(row: Dict[str, str], ignore_definition_expression: bool) -> str:
    if ignore_definition_expression:
        return "1=1"
    expr = (row.get("definition_expression") or row.get("definition_expression_from_webmap") or "").strip()
    return expr if expr else "1=1"


def check_layer_health(row: Dict[str, str], token: Optional[str], timeout: int, ignore_definition_expression: bool, auth_first: bool) -> LayerHealthDetailRow:
    layer_url = (row.get("url") or row.get("layer_url") or "").strip()
    service_host, service_path, internal_service_detected, internal_service_reason = classify_service_host(layer_url)
    where = determine_query_where(row, ignore_definition_expression)

    webmap_item_id = row.get("webmap_item_id", "")
    webmap_title = row.get("webmap_title", "")
    layer_id = row.get("layer_id", "")
    layer_title = row.get("layer_title", "")
    layer_type_from_webmap = row.get("layer_type", "")
    layer_item_id = row.get("item_id", "")
    visibility = row.get("visibility", "")
    has_def_expr = parse_boolish(row.get("has_definition_expression"))
    def_expr = row.get("definition_expression", "")

    if not is_valid_url(layer_url):
        status, risk, score, issues, recommendation = evaluate_health(
            endpoint_reachable=False,
            token_denied=False,
            invalid_token_seen=False,
            metadata_error="Layer URL is missing or invalid.",
            metadata_time=None,
            query_supported=False,
            count_attempted=False,
            count_success=False,
            count_error="",
            sample_attempted=False,
            sample_success=False,
            sample_error="",
            access_mode_used="none",
            internal_service_detected=internal_service_detected,
            internal_service_reason=internal_service_reason,
        )

        return LayerHealthDetailRow(
            scan_timestamp_utc=now_utc_string(),
            webmap_item_id=webmap_item_id,
            webmap_title=webmap_title,
            layer_id=layer_id,
            layer_title=layer_title,
            layer_type_from_webmap=layer_type_from_webmap,
            layer_url=layer_url,
            service_host=service_host,
            service_path=service_path,
            internal_service_detected=internal_service_detected,
            internal_service_reason=internal_service_reason,
            layer_item_id=layer_item_id,
            visibility=visibility,
            has_definition_expression_from_webmap=has_def_expr,
            definition_expression_from_webmap=def_expr,
            access_mode_used="none",
            anonymous_metadata_success=False,
            authenticated_metadata_attempted=False,
            authenticated_metadata_success=False,
            endpoint_reachable=False,
            metadata_status_code="",
            metadata_response_time_sec="",
            metadata_error="Layer URL is missing or invalid.",
            token_required_or_access_denied=False,
            invalid_token_seen=False,
            service_current_version="",
            service_type="",
            geometry_type="",
            object_id_field="",
            display_field="",
            fields_count="",
            max_record_count="",
            capabilities="",
            supports_query=False,
            supports_statistics=False,
            supports_advanced_queries=False,
            supports_pagination=False,
            supports_order_by=False,
            supports_distinct=False,
            editing_enabled=False,
            attachments_enabled="",
            has_z="",
            has_m="",
            extent_available=False,
            count_query_attempted=False,
            count_query_success=False,
            count_query_response_time_sec="",
            feature_count="",
            count_query_error="",
            sample_query_attempted=False,
            sample_query_success=False,
            sample_query_response_time_sec="",
            sample_feature_count_returned="",
            sample_query_error="",
            health_status=status,
            risk_level=risk,
            risk_score=score,
            issue_summary=issues,
            recommendation=recommendation,
        )

    access_result = get_metadata_with_access_strategy(layer_url, token=token, timeout=timeout, auth_first=auth_first)
    metadata_success = bool(access_result["success"])
    metadata_status_code = safe_str(access_result["status_code"])
    metadata_time = access_result["elapsed"]
    metadata_json = access_result["data"]
    metadata_error = safe_str(access_result["error"])
    access_mode_used = safe_str(access_result["access_mode_used"])
    invalid_token_seen = bool(access_result["invalid_token_seen"])
    token_denied = bool(access_result["token_or_access_denied"]) and not metadata_success

    metadata = metadata_json if isinstance(metadata_json, dict) and metadata_success else {}

    query_supported = supports_query(metadata) if metadata else False

    # Use token for query only if the successful metadata access mode was authenticated.
    query_token = token if access_mode_used.startswith("authenticated") else None

    count_attempted = bool(metadata_success and query_supported)
    count_success = False
    count_time: Optional[float] = None
    feature_count = ""
    count_error = ""

    if count_attempted:
        count_success, count_time, feature_count, count_error = run_count_query(
            layer_url=layer_url,
            token=query_token,
            where=where,
            timeout=timeout,
        )

    sample_attempted = bool(metadata_success and query_supported)
    sample_success = False
    sample_time: Optional[float] = None
    sample_returned = ""
    sample_error = ""

    if sample_attempted:
        sample_success, sample_time, sample_returned, sample_error = run_sample_query(
            layer_url=layer_url,
            token=query_token,
            where=where,
            timeout=timeout,
            sample_size=5,
        )

    status, risk, score, issues, recommendation = evaluate_health(
        endpoint_reachable=metadata_success,
        token_denied=token_denied,
        invalid_token_seen=invalid_token_seen,
        metadata_error=metadata_error,
        metadata_time=metadata_time,
        query_supported=query_supported,
        count_attempted=count_attempted,
        count_success=count_success,
        count_error=count_error,
        sample_attempted=sample_attempted,
        sample_success=sample_success,
        sample_error=sample_error,
        access_mode_used=access_mode_used,
        internal_service_detected=internal_service_detected,
        internal_service_reason=internal_service_reason,
    )

    return LayerHealthDetailRow(
        scan_timestamp_utc=now_utc_string(),
        webmap_item_id=webmap_item_id,
        webmap_title=webmap_title,
        layer_id=layer_id,
        layer_title=layer_title,
        layer_type_from_webmap=layer_type_from_webmap,
        layer_url=layer_url,
        service_host=service_host,
        service_path=service_path,
        internal_service_detected=internal_service_detected,
        internal_service_reason=internal_service_reason,
        layer_item_id=layer_item_id,
        visibility=visibility,
        has_definition_expression_from_webmap=has_def_expr,
        definition_expression_from_webmap=def_expr,
        access_mode_used=access_mode_used,
        anonymous_metadata_success=bool(access_result["anonymous_success"]),
        authenticated_metadata_attempted=bool(access_result["authenticated_attempted"]),
        authenticated_metadata_success=bool(access_result["authenticated_success"]),
        endpoint_reachable=metadata_success,
        metadata_status_code=metadata_status_code,
        metadata_response_time_sec=format_seconds(metadata_time),
        metadata_error=metadata_error,
        token_required_or_access_denied=token_denied,
        invalid_token_seen=invalid_token_seen,
        service_current_version=safe_str(metadata.get("currentVersion")),
        service_type=safe_str(metadata.get("type")),
        geometry_type=safe_str(metadata.get("geometryType")),
        object_id_field=get_object_id_field(metadata),
        display_field=safe_str(metadata.get("displayField")),
        fields_count=fields_count(metadata),
        max_record_count=safe_str(metadata.get("maxRecordCount")),
        capabilities=safe_str(metadata.get("capabilities")),
        supports_query=query_supported,
        supports_statistics=get_advanced_query_capability(metadata, "supportsStatistics"),
        supports_advanced_queries=get_advanced_query_capability(metadata, "supportsAdvancedQueries"),
        supports_pagination=get_advanced_query_capability(metadata, "supportsPagination"),
        supports_order_by=get_advanced_query_capability(metadata, "supportsOrderBy"),
        supports_distinct=get_advanced_query_capability(metadata, "supportsDistinct"),
        editing_enabled=editing_enabled(metadata),
        attachments_enabled=safe_str(metadata.get("hasAttachments")),
        has_z=safe_str(metadata.get("hasZ")),
        has_m=safe_str(metadata.get("hasM")),
        extent_available=extent_available(metadata),
        count_query_attempted=count_attempted,
        count_query_success=count_success,
        count_query_response_time_sec=format_seconds(count_time),
        feature_count=feature_count,
        count_query_error=count_error,
        sample_query_attempted=sample_attempted,
        sample_query_success=sample_success,
        sample_query_response_time_sec=format_seconds(sample_time),
        sample_feature_count_returned=sample_returned,
        sample_query_error=sample_error,
        health_status=status,
        risk_level=risk,
        risk_score=score,
        issue_summary=issues,
        recommendation=recommendation,
    )


def build_summary(input_file: str, rows_read: int, detail_rows: List[LayerHealthDetailRow], auth_first: bool) -> LayerHealthSummaryRow:
    def count_where(predicate) -> int:
        return sum(1 for row in detail_rows if predicate(row))

    metadata_times = [parse_float(row.metadata_response_time_sec) for row in detail_rows]
    metadata_times = [v for v in metadata_times if v is not None]
    count_times = [parse_float(row.count_query_response_time_sec) for row in detail_rows]
    count_times = [v for v in count_times if v is not None]

    avg_metadata = sum(metadata_times) / len(metadata_times) if metadata_times else None
    avg_count = sum(count_times) / len(count_times) if count_times else None

    slow_layer_count = sum(1 for v in metadata_times if v > 3)

    unreachable = count_where(lambda r: not r.endpoint_reachable)
    access_denied = count_where(lambda r: r.token_required_or_access_denied)
    high_or_critical = count_where(lambda r: r.risk_level in {"high", "critical"})
    public_reachable = count_where(lambda r: r.access_mode_used.startswith("anonymous"))
    auth_success = count_where(lambda r: r.access_mode_used.startswith("authenticated") and r.endpoint_reachable)
    auth_required_not_tested = count_where(lambda r: not r.endpoint_reachable and not r.authenticated_metadata_attempted and r.token_required_or_access_denied)
    invalid_token_seen = count_where(lambda r: r.invalid_token_seen)
    internal_service_count = count_where(lambda r: r.internal_service_detected)
    internal_service_unreachable_count = count_where(lambda r: r.internal_service_detected and not r.endpoint_reachable)

    issues = []
    if unreachable:
        issues.append(f"{unreachable} layer endpoint(s) were unreachable or returned metadata errors.")
    if access_denied:
        issues.append(f"{access_denied} layer endpoint(s) appear to require a token or deny access.")
    if invalid_token_seen:
        issues.append(f"{invalid_token_seen} layer endpoint(s) produced invalid-token behavior during fallback testing.")
    if internal_service_count:
        issues.append(f"{internal_service_count} layer endpoint(s) appear to reference internal/dev/test service hosts or paths.")
    if internal_service_unreachable_count:
        issues.append(f"{internal_service_unreachable_count} internal/dev/test endpoint(s) were unreachable.")
    if slow_layer_count:
        issues.append(f"{slow_layer_count} layer endpoint(s) had metadata response time over 3 seconds.")
    if high_or_critical:
        issues.append(f"{high_or_critical} layer endpoint(s) are high or critical risk.")
    if not issues:
        issues.append("All checked layer endpoints appear healthy based on lightweight REST checks.")

    return LayerHealthSummaryRow(
        scan_timestamp_utc=now_utc_string(),
        input_file=input_file,
        access_strategy="auth-first" if auth_first else "anonymous-first",
        total_layer_rows_read=rows_read,
        unique_layer_urls_checked=len(detail_rows),
        internal_service_count=internal_service_count,
        internal_service_unreachable_count=internal_service_unreachable_count,
        reachable_count=count_where(lambda r: r.endpoint_reachable),
        unreachable_count=unreachable,
        public_reachable_count=public_reachable,
        auth_required_and_auth_success_count=auth_success,
        auth_required_but_not_tested_count=auth_required_not_tested,
        token_or_access_denied_count=access_denied,
        invalid_token_seen_count=invalid_token_seen,
        query_supported_count=count_where(lambda r: r.supports_query),
        count_query_success_count=count_where(lambda r: r.count_query_success),
        sample_query_success_count=count_where(lambda r: r.sample_query_success),
        low_risk_count=count_where(lambda r: r.risk_level == "low"),
        medium_risk_count=count_where(lambda r: r.risk_level == "medium"),
        high_risk_count=count_where(lambda r: r.risk_level == "high"),
        critical_risk_count=count_where(lambda r: r.risk_level == "critical"),
        average_metadata_response_time_sec=format_seconds(avg_metadata),
        average_count_query_response_time_sec=format_seconds(avg_count),
        slow_layer_count=slow_layer_count,
        issue_summary=" | ".join(issues),
    )


# -----------------------------------------------------------------------------
# CLI
# -----------------------------------------------------------------------------

def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Check REST health and query behavior for layers exported by Script 03."
    )
    parser.add_argument(
        "--webmap-layers-csv",
        required=True,
        help="Path to webmap_layers_*.csv exported by Script 03.",
    )
    parser.add_argument(
        "--portal",
        default=DEFAULT_PORTAL,
        help="Portal URL. Example: https://www.arcgis.com or https://yourorg.maps.arcgis.com",
    )
    parser.add_argument(
        "--username",
        default=None,
        help="Portal username. If supplied, authenticated fallback is available after anonymous failure.",
    )
    parser.add_argument(
        "--anonymous",
        action="store_true",
        help="Force scan without ArcGIS token. Anonymous-only mode.",
    )
    parser.add_argument(
        "--auth-first",
        action="store_true",
        help="Test authenticated access first, then retry anonymous only if invalid-token behavior is detected. Default is anonymous-first.",
    )
    parser.add_argument(
        "--timeout",
        type=int,
        default=DEFAULT_TIMEOUT,
        help="Request timeout in seconds. Default: 30.",
    )
    parser.add_argument(
        "--ignore-definition-expression",
        action="store_true",
        help="Use WHERE 1=1 for test queries instead of the web map definition expression.",
    )
    parser.add_argument(
        "--output-prefix",
        default=None,
        help="Optional output filename prefix. Defaults to layer_health timestamp.",
    )
    return parser.parse_args()


def main() -> int:
    log_path = setup_logging()
    args = parse_args()

    try:
        layers_path = Path(args.webmap_layers_csv)
        if not layers_path.exists():
            raise FileNotFoundError(f"Web map layers CSV not found: {layers_path}")

        timestamp = dt.now().strftime("%Y%m%d_%H%M%S")
        output_prefix = args.output_prefix or f"layer_health_{timestamp}"

        logging.info("Starting LHI ExB App Inspector - Script 04")
        logging.info("Web map layers CSV: %s", layers_path)
        logging.info("Access strategy: %s", "auth-first" if args.auth_first else "anonymous-first")

        raw_rows = read_csv_dicts(layers_path)
        layer_rows = dedupe_layer_rows(raw_rows)

        logging.info("Layer rows read: %s | unique rows to check: %s", len(raw_rows), len(layer_rows))

        token = get_portal_token(
            portal_url=args.portal,
            username=args.username,
            anonymous=args.anonymous,
        )

        detail_rows: List[LayerHealthDetailRow] = []
        for index, row in enumerate(layer_rows, start=1):
            title = row.get("layer_title", "")
            url = row.get("url", "")
            logging.info("Checking layer %s/%s: %s | %s", index, len(layer_rows), title, url)

            detail = check_layer_health(
                row=row,
                token=token,
                timeout=args.timeout,
                ignore_definition_expression=args.ignore_definition_expression,
                auth_first=args.auth_first,
            )
            detail_rows.append(detail)

        summary_row = build_summary(layers_path.name, len(raw_rows), detail_rows, args.auth_first)

        summary_csv = CSV_DIR / f"layer_health_summary_{output_prefix}_{timestamp}.csv"
        details_csv = CSV_DIR / f"layer_health_details_{output_prefix}_{timestamp}.csv"

        write_csv(summary_csv, [summary_row])
        write_csv(details_csv, detail_rows)

        print("\n=== LHI ExB App Inspector: Script 04 Complete ===")
        print(f"Access strategy: {summary_row.access_strategy}")
        print(f"Layer rows read: {len(raw_rows)}")
        print(f"Unique layer URLs checked: {len(detail_rows)}")
        print(f"Reachable: {summary_row.reachable_count}")
        print(f"Internal/dev/test service endpoints detected: {summary_row.internal_service_count}")
        print(f"Internal/dev/test endpoints unreachable: {summary_row.internal_service_unreachable_count}")
        print(f"Public/anonymous reachable: {summary_row.public_reachable_count}")
        print(f"Authenticated reachable after fallback: {summary_row.auth_required_and_auth_success_count}")
        print(f"Unreachable/errors: {summary_row.unreachable_count}")
        print(f"Token/access denied: {summary_row.token_or_access_denied_count}")
        print(f"Invalid token behavior seen: {summary_row.invalid_token_seen_count}")
        print(f"Query supported: {summary_row.query_supported_count}")
        print(f"Count query success: {summary_row.count_query_success_count}")
        print(f"Sample query success: {summary_row.sample_query_success_count}")
        print(f"Low risk: {summary_row.low_risk_count}")
        print(f"Medium risk: {summary_row.medium_risk_count}")
        print(f"High risk: {summary_row.high_risk_count}")
        print(f"Critical risk: {summary_row.critical_risk_count}")
        print(f"Average metadata response time: {summary_row.average_metadata_response_time_sec}s")
        print(f"Average count query response time: {summary_row.average_count_query_response_time_sec}s")
        print("\nOutputs:")
        print(f"Layer health summary CSV: {summary_csv}")
        print(f"Layer health details CSV: {details_csv}")
        print(f"Log file: {log_path}")

        if summary_row.issue_summary:
            print("\nSummary:")
            print(summary_row.issue_summary)

        return 0

    except Exception as exc:
        logging.exception("Layer health check failed: %s", exc)
        print("\nLayer health check failed. Check the log file for details:")
        print(log_path)
        print(f"Error: {exc}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
