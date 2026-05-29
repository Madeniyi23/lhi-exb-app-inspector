"""
LHI ExB App Inspector
Script 05: Sharing Compatibility Checker v0.8.6 + Interactive HTML Report

Purpose:
- Combine outputs from Scripts 01, 03, and 04
- Compare ExB app sharing, web map sharing, layer access mode, and dependency resolution
- Identify likely sharing/access mismatch risks
- Treat unmatched health rows with no active dependencies as informational instead of forcing Script 04 reruns
- Classify internal/dev/test service endpoints as access-design risks when relevant
- Avoid false alarms when app is org-shared but web map/layers are public
- Produce CSV outputs plus an interactive HTML report

Inputs:
- app_summary_*.csv from Script 01
- webmap_summary_*.csv from Script 03
- webmap_layers_*.csv from Script 03
- exb_layer_reference_resolution_*.csv from Script 03
- layer_health_details_*.csv from Script 04

Author: Lazy Hat Innovations
"""

from __future__ import annotations

import argparse
import csv
import html
import logging
import re
import sys
from dataclasses import dataclass, asdict
from datetime import datetime as dt, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import urlparse


# -----------------------------------------------------------------------------
# Defaults
# -----------------------------------------------------------------------------

OUTPUT_ROOT = Path("outputs")
CSV_DIR = OUTPUT_ROOT / "csv"
REPORT_DIR = OUTPUT_ROOT / "reports"
LOG_DIR = OUTPUT_ROOT / "logs"


# -----------------------------------------------------------------------------
# Data models
# -----------------------------------------------------------------------------

@dataclass
class SharingCompatibilityDetailRow:
    scan_timestamp_utc: str
    app_item_id: str
    app_title: str
    app_access: str
    webmap_item_id: str
    webmap_title: str
    webmap_access: str
    layer_id: str
    layer_title: str
    layer_url: str
    service_host: str
    internal_service_detected: bool
    internal_service_reason: str
    layer_visibility: str
    layer_access_mode_used: str
    layer_endpoint_reachable: bool
    layer_token_required_or_access_denied: bool
    layer_health_status: str
    layer_risk_level: str
    active_dependency_count: int
    active_dependency_widgets: str
    template_residue_dependency_count: int
    sharing_status: str
    severity: str
    risk_score: int
    issue_summary: str
    recommendation: str


@dataclass
class SharingCompatibilitySummaryRow:
    scan_timestamp_utc: str
    app_item_id: str
    app_title: str
    app_access: str
    webmap_count: int
    webmap_access_summary: str
    layer_count: int
    public_reachable_layer_count: int
    authenticated_layer_count: int
    inaccessible_layer_count: int
    active_dependency_count: int
    active_dependency_layer_count: int
    template_residue_dependency_count: int
    possible_broken_dependency_count: int
    internal_service_layer_count: int
    internal_service_unreachable_count: int
    low_risk_count: int
    medium_risk_count: int
    high_risk_count: int
    critical_risk_count: int
    overall_status: str
    overall_risk_level: str
    overall_risk_score: int
    operational_risk_level: str
    maintenance_note_level: str
    issue_summary: str
    recommendation: str


@dataclass
class SharingRecommendationRow:
    scan_timestamp_utc: str
    priority: str
    affected_item_type: str
    affected_item_title: str
    affected_item_id_or_url: str
    issue: str
    recommendation: str
    evidence: str


# -----------------------------------------------------------------------------
# Setup and utility functions
# -----------------------------------------------------------------------------

def setup_output_dirs() -> None:
    for folder in [OUTPUT_ROOT, CSV_DIR, REPORT_DIR, LOG_DIR]:
        folder.mkdir(parents=True, exist_ok=True)


def setup_logging() -> Path:
    setup_output_dirs()
    timestamp = dt.now().strftime("%Y%m%d_%H%M%S")
    log_path = LOG_DIR / f"check_sharing_compatibility_{timestamp}.log"

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
    return str(value)


def esc(value: Any) -> str:
    return html.escape(safe_str(value), quote=True)


def parse_boolish(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if value is None:
        return False
    text = str(value).strip().lower()
    return text in {"true", "1", "yes", "y"}


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


def first_row(rows: List[Dict[str, str]], name: str) -> Dict[str, str]:
    if not rows:
        raise RuntimeError(f"{name} is empty or could not be read.")
    return rows[0]


def normalize_url(url: str) -> str:
    return (url or "").strip().rstrip("/").lower()


def classify_service_host_from_url(url: str) -> Tuple[str, bool, str]:
    """
    Fallback host classifier for service URL identity/sharing checks.

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


def normalize_id(value: str) -> str:
    return (value or "").strip().lower()


def join_sorted(values: List[str]) -> str:
    cleaned = sorted({str(v).strip() for v in values if str(v).strip()})
    return "; ".join(cleaned)


def access_rank(access: str) -> int:
    access = (access or "").strip().lower()
    if access in {"private", "none", ""}:
        return 0
    if access in {"shared", "group", "groups"}:
        return 1
    if access in {"org", "organization"}:
        return 2
    if access == "public":
        return 3
    return 0


def is_public_access(access: str) -> bool:
    return (access or "").strip().lower() == "public"


# -----------------------------------------------------------------------------
# Index helpers
# -----------------------------------------------------------------------------

def build_health_by_layer_url(layer_health_rows: List[Dict[str, str]]) -> Dict[str, Dict[str, str]]:
    index: Dict[str, Dict[str, str]] = {}
    for row in layer_health_rows:
        url = normalize_url(row.get("layer_url") or row.get("url") or "")
        if url:
            index[url] = row
    return index


def build_active_dependency_index(resolution_rows: List[Dict[str, str]]) -> Dict[str, List[Dict[str, str]]]:
    index: Dict[str, List[Dict[str, str]]] = {}
    for row in resolution_rows:
        status = row.get("resolution_status", "")
        if status.startswith("resolved_active_webmap_layer"):
            url = normalize_url(row.get("resolved_webmap_layer_url", ""))
            if url:
                index.setdefault(url, []).append(row)
    return index


def count_by_status(resolution_rows: List[Dict[str, str]], status_value: str) -> int:
    return sum(1 for row in resolution_rows if row.get("resolution_status") == status_value)


def count_template_residue(resolution_rows: List[Dict[str, str]]) -> int:
    return count_by_status(resolution_rows, "unresolved_likely_template_residue")


def count_possible_broken_dependencies(resolution_rows: List[Dict[str, str]]) -> int:
    return count_by_status(resolution_rows, "unresolved_possible_broken_dependency")


def count_active_dependencies(resolution_rows: List[Dict[str, str]]) -> int:
    return sum(1 for row in resolution_rows if row.get("resolution_status", "").startswith("resolved_active_webmap_layer"))


def count_active_dependency_layers(resolution_rows: List[Dict[str, str]]) -> int:
    urls = set()
    for row in resolution_rows:
        if row.get("resolution_status", "").startswith("resolved_active_webmap_layer"):
            url = normalize_url(row.get("resolved_webmap_layer_url", ""))
            if url:
                urls.add(url)
    return len(urls)


# -----------------------------------------------------------------------------
# Compatibility logic
# -----------------------------------------------------------------------------

def evaluate_layer_sharing(
    app_access: str,
    webmap_access: str,
    layer_row: Dict[str, str],
    health_row: Optional[Dict[str, str]],
    active_deps: List[Dict[str, str]],
) -> Tuple[str, str, int, str, str]:
    if health_row is None:
        active_count = len(active_deps)
        layer_url = (layer_row.get("url") or "").strip()
        layer_visibility = str(layer_row.get("visibility") or "").strip().lower()
        layer_type = str(layer_row.get("layer_type") or "").strip()

        if active_count > 0:
            return (
                "health_check_missing_for_active_dependency",
                "warning",
                35,
                "Layer is actively referenced by one or more widgets, but no matching layer health record was found.",
                "Review this layer. Rerun Script 04 only if the layer has a valid REST URL and should support widget queries.",
            )

        if not layer_url:
            return (
                "layer_health_not_checked_no_rest_url",
                "info",
                0,
                "Layer was found in the web map but has no REST URL, so Script 04 could not perform a REST health check. No active widget dependency was detected.",
                "No immediate action required unless this layer is visible/user-facing or expected to support query/list/table widgets.",
            )

        return (
            "layer_health_not_checked_or_not_required",
            "info" if layer_visibility in {"false", "0", "no"} else "review",
            5 if layer_visibility in {"true", "1", "yes"} else 0,
            "Layer was found in the web map but was not matched to a health-check result. No active widget dependency was detected.",
            "Treat as informational unless this layer is visible, user-facing, or expected to support widget queries. Rerun Script 04 only if this layer should have been checked.",
        )

    endpoint_reachable = parse_boolish(health_row.get("endpoint_reachable"))
    access_mode = health_row.get("access_mode_used", "")
    token_denied = parse_boolish(health_row.get("token_required_or_access_denied"))
    health_risk = (health_row.get("risk_level") or "").lower()

    fallback_host, fallback_internal, fallback_reason = classify_service_host_from_url(layer_row.get("url", ""))
    internal_service_detected = parse_boolish(health_row.get("internal_service_detected")) or fallback_internal
    internal_service_reason = health_row.get("internal_service_reason") or fallback_reason
    service_host = health_row.get("service_host") or fallback_host

    active_count = len(active_deps)

    if not endpoint_reachable:
        if internal_service_detected:
            score = 75 if active_count else 45
            severity = "critical" if active_count else "warning"
            return (
                "internal_network_layer_access_risk" if active_count else "internal_network_layer_not_reachable",
                severity,
                score,
                f"Layer endpoint is not reachable and appears to reference an internal/dev/test service endpoint ({internal_service_reason}). This is more serious if active widgets depend on it.",
                "If this app is public or broadly shared, replace this internal service with a public/hosted view or restrict the app/web map to the intended internal audience. If it is internal-only, confirm users are on the network/VPN and the service is available.",
            )

        score = 70 if active_count else 45
        severity = "critical" if active_count else "warning"
        return (
            "inaccessible_layer",
            severity,
            score,
            "Layer endpoint is not reachable. This is more serious if active widgets depend on it.",
            "Check the REST service URL, service status, and whether the intended app audience has access.",
        )

    if token_denied:
        score = 60 if active_count else 35
        severity = "high" if active_count else "review"
        return (
            "restricted_or_token_required_layer",
            severity,
            score,
            "Layer appears to require authentication or denied access during health testing.",
            "Confirm the layer is shared with the same audience as the Experience Builder app and web map.",
        )

    if access_mode.startswith("anonymous"):
        if internal_service_detected and is_public_access(webmap_access):
            return (
                "internal_service_in_public_webmap",
                "review",
                15,
                f"Layer is reachable but appears to reference an internal/dev/test service endpoint ({internal_service_reason}) while the web map is public.",
                "Confirm whether this internal-looking service is intentionally exposed. For public-facing apps, prefer a public/hosted view or public-safe service URL.",
            )

        return (
            "compatible_public_layer",
            "ok",
            0,
            "Layer is reachable anonymously. This is compatible with the app/web map audience.",
            "No sharing action required.",
        )

    if access_mode.startswith("authenticated"):
        if (app_access or "").strip().lower() == "public":
            return (
                "public_app_uses_authenticated_layer",
                "high",
                55,
                "The app appears public, but the layer required authenticated access during testing.",
                "Share the layer publicly if intended for public use, or restrict the app audience to match the layer.",
            )

        if internal_service_detected and is_public_access(webmap_access):
            return (
                "authenticated_internal_service_in_public_webmap",
                "review",
                20,
                f"Layer is reachable with authentication but appears to reference an internal/dev/test service endpoint ({internal_service_reason}) while the web map is public.",
                "Confirm whether the public web map should reference this internal service. For broad-use apps, consider a properly shared hosted view or restrict web map sharing.",
            )

        return (
            "compatible_authenticated_layer",
            "ok",
            5,
            "Layer is reachable with authenticated access. This may be compatible for org/internal apps.",
            "Confirm the same users/groups who can open the app can also access this layer.",
        )

    if health_risk in {"high", "critical"}:
        return (
            "layer_health_risk_affects_sharing_confidence",
            health_risk,
            50,
            "Layer health risk is high enough that sharing compatibility cannot be confirmed confidently.",
            "Resolve layer health issues before final sharing diagnosis.",
        )

    return (
        "needs_manual_review",
        "review",
        20,
        "Layer access mode could not be confidently interpreted.",
        "Review the layer health details and sharing configuration manually.",
    )


def make_detail_rows(
    app_row: Dict[str, str],
    webmap_summary_rows: List[Dict[str, str]],
    webmap_layer_rows: List[Dict[str, str]],
    resolution_rows: List[Dict[str, str]],
    layer_health_rows: List[Dict[str, str]],
) -> List[SharingCompatibilityDetailRow]:
    app_item_id = app_row.get("app_item_id", "")
    app_title = app_row.get("title") or app_row.get("app_title", "")
    app_access = app_row.get("access", "")

    webmap_by_id = {normalize_id(row.get("webmap_item_id", "")): row for row in webmap_summary_rows}
    health_by_url = build_health_by_layer_url(layer_health_rows)
    active_dependency_by_url = build_active_dependency_index(resolution_rows)
    template_residue_total = count_template_residue(resolution_rows)

    rows: List[SharingCompatibilityDetailRow] = []

    for layer in webmap_layer_rows:
        webmap_item_id = layer.get("webmap_item_id", "")
        webmap_summary = webmap_by_id.get(normalize_id(webmap_item_id), {})
        webmap_title = webmap_summary.get("webmap_title", layer.get("webmap_title", ""))
        webmap_access = webmap_summary.get("access", "")
        layer_url = layer.get("url", "")
        health = health_by_url.get(normalize_url(layer_url))
        active_deps = active_dependency_by_url.get(normalize_url(layer_url), [])

        sharing_status, severity, risk_score, issue, recommendation = evaluate_layer_sharing(
            app_access=app_access,
            webmap_access=webmap_access,
            layer_row=layer,
            health_row=health,
            active_deps=active_deps,
        )

        active_widgets = join_sorted([
            f"{dep.get('widget_id', '')} ({dep.get('widget_label', '')})"
            for dep in active_deps
        ])

        rows.append(
            SharingCompatibilityDetailRow(
                scan_timestamp_utc=now_utc_string(),
                app_item_id=app_item_id,
                app_title=app_title,
                app_access=app_access,
                webmap_item_id=webmap_item_id,
                webmap_title=webmap_title,
                webmap_access=webmap_access,
                layer_id=layer.get("layer_id", ""),
                layer_title=layer.get("layer_title", ""),
                layer_url=layer_url,
                service_host=(health.get("service_host", "") if health else classify_service_host_from_url(layer_url)[0]),
                internal_service_detected=(parse_boolish(health.get("internal_service_detected")) if health else classify_service_host_from_url(layer_url)[1]),
                internal_service_reason=(health.get("internal_service_reason", "") if health else classify_service_host_from_url(layer_url)[2]),
                layer_visibility=layer.get("visibility", ""),
                layer_access_mode_used=health.get("access_mode_used", "") if health else "",
                layer_endpoint_reachable=parse_boolish(health.get("endpoint_reachable")) if health else False,
                layer_token_required_or_access_denied=parse_boolish(health.get("token_required_or_access_denied")) if health else False,
                layer_health_status=health.get("health_status", "") if health else "",
                layer_risk_level=health.get("risk_level", "") if health else "",
                active_dependency_count=len(active_deps),
                active_dependency_widgets=active_widgets,
                template_residue_dependency_count=template_residue_total,
                sharing_status=sharing_status,
                severity=severity,
                risk_score=risk_score,
                issue_summary=issue,
                recommendation=recommendation,
            )
        )

    return rows


def make_summary_row(
    app_row: Dict[str, str],
    webmap_summary_rows: List[Dict[str, str]],
    detail_rows: List[SharingCompatibilityDetailRow],
    resolution_rows: List[Dict[str, str]],
) -> SharingCompatibilitySummaryRow:
    app_item_id = app_row.get("app_item_id", "")
    app_title = app_row.get("title") or app_row.get("app_title", "")
    app_access = app_row.get("access", "")

    webmap_access_summary = join_sorted([row.get("access", "") for row in webmap_summary_rows])
    public_reachable = sum(1 for row in detail_rows if row.layer_access_mode_used.startswith("anonymous") and row.layer_endpoint_reachable)
    authenticated = sum(1 for row in detail_rows if row.layer_access_mode_used.startswith("authenticated") and row.layer_endpoint_reachable)
    inaccessible = sum(1 for row in detail_rows if not row.layer_endpoint_reachable)

    low = sum(1 for row in detail_rows if row.severity in {"ok", "info"})
    medium = sum(1 for row in detail_rows if row.severity == "review")
    high = sum(1 for row in detail_rows if row.severity in {"warning", "high"})
    critical = sum(1 for row in detail_rows if row.severity == "critical")

    active_dependency_count = count_active_dependencies(resolution_rows)
    active_dependency_layer_count = count_active_dependency_layers(resolution_rows)
    template_residue_count = count_template_residue(resolution_rows)
    possible_broken_count = count_possible_broken_dependencies(resolution_rows)
    internal_service_count = sum(1 for row in detail_rows if row.internal_service_detected)
    internal_service_unreachable_count = sum(1 for row in detail_rows if row.internal_service_detected and not row.layer_endpoint_reachable)

    operational_score = sum(row.risk_score for row in detail_rows)
    maintenance_score = min(template_residue_count, 10) if template_residue_count else 0
    overall_score = operational_score + maintenance_score
    if possible_broken_count:
        overall_score += possible_broken_count * 25

    if critical or operational_score >= 100:
        operational_risk = "critical"
    elif high or possible_broken_count or operational_score >= 50:
        operational_risk = "high"
    elif medium or operational_score >= 15:
        operational_risk = "medium"
    else:
        operational_risk = "low"

    if template_residue_count:
        maintenance_note_level = "info"
    else:
        maintenance_note_level = "none"

    if operational_risk == "critical":
        overall_status = "fail"
        overall_risk = "critical"
    elif operational_risk == "high":
        overall_status = "warning"
        overall_risk = "high"
    elif operational_risk == "medium":
        overall_status = "review"
        overall_risk = "medium"
    else:
        overall_status = "ok"
        overall_risk = "low"

    issues: List[str] = []
    recommendations: List[str] = []

    if inaccessible:
        issues.append(f"{inaccessible} layer(s) are not reachable.")
        recommendations.append("Review inaccessible layer URLs and sharing permissions.")

    if possible_broken_count:
        issues.append(f"{possible_broken_count} possible broken active widget dependency/dependencies were detected.")
        recommendations.append("Reconnect or remove widgets that point to missing active data sources.")

    if internal_service_count:
        issues.append(f"{internal_service_count} layer(s) appear to reference internal/dev/test service endpoints.")
        recommendations.append("Confirm whether internal service URLs are appropriate for the app/web map audience.")

    if template_residue_count:
        issues.append(f"{template_residue_count} likely template/copy residue dependency references were detected.")
        recommendations.append("Treat template residue as informational unless related widgets are visible and failing.")

    if not issues:
        issues.append("No sharing compatibility issues detected from the available scan outputs.")
        recommendations.append("No immediate sharing action required.")

    return SharingCompatibilitySummaryRow(
        scan_timestamp_utc=now_utc_string(),
        app_item_id=app_item_id,
        app_title=app_title,
        app_access=app_access,
        webmap_count=len(webmap_summary_rows),
        webmap_access_summary=webmap_access_summary,
        layer_count=len(detail_rows),
        public_reachable_layer_count=public_reachable,
        authenticated_layer_count=authenticated,
        inaccessible_layer_count=inaccessible,
        active_dependency_count=active_dependency_count,
        active_dependency_layer_count=active_dependency_layer_count,
        template_residue_dependency_count=template_residue_count,
        possible_broken_dependency_count=possible_broken_count,
        internal_service_layer_count=internal_service_count,
        internal_service_unreachable_count=internal_service_unreachable_count,
        low_risk_count=low,
        medium_risk_count=medium,
        high_risk_count=high,
        critical_risk_count=critical,
        overall_status=overall_status,
        overall_risk_level=overall_risk,
        overall_risk_score=overall_score,
        operational_risk_level=operational_risk,
        maintenance_note_level=maintenance_note_level,
        issue_summary=" | ".join(issues),
        recommendation=" | ".join(recommendations),
    )


def make_recommendations(summary: SharingCompatibilitySummaryRow, detail_rows: List[SharingCompatibilityDetailRow]) -> List[SharingRecommendationRow]:
    rows: List[SharingRecommendationRow] = []

    for detail in detail_rows:
        if detail.severity in {"critical", "high", "warning"}:
            rows.append(
                SharingRecommendationRow(
                    scan_timestamp_utc=now_utc_string(),
                    priority="High" if detail.severity in {"critical", "high"} else "Medium",
                    affected_item_type="Layer",
                    affected_item_title=detail.layer_title,
                    affected_item_id_or_url=detail.layer_url,
                    issue=detail.issue_summary,
                    recommendation=detail.recommendation,
                    evidence=(
                        f"App access={detail.app_access}; Web map access={detail.webmap_access}; "
                        f"Layer access mode={detail.layer_access_mode_used}; Active dependency count={detail.active_dependency_count}"
                    ),
                )
            )

    if summary.template_residue_dependency_count:
        rows.append(
            SharingRecommendationRow(
                scan_timestamp_utc=now_utc_string(),
                priority="Info",
                affected_item_type="Experience Builder App Config",
                affected_item_title=summary.app_title,
                affected_item_id_or_url=summary.app_item_id,
                issue=f"{summary.template_residue_dependency_count} likely copied-template/stale embedded references were found.",
                recommendation="Keep as informational. Clean up only if the app becomes difficult to maintain or if visible widgets fail.",
                evidence="Script 03 classified unresolved embedded config expressions as likely template residue.",
            )
        )

    if not rows:
        rows.append(
            SharingRecommendationRow(
                scan_timestamp_utc=now_utc_string(),
                priority="None",
                affected_item_type="App",
                affected_item_title=summary.app_title,
                affected_item_id_or_url=summary.app_item_id,
                issue="No sharing compatibility issue detected.",
                recommendation="No immediate action required.",
                evidence=f"Overall status={summary.overall_status}; Risk={summary.overall_risk_level}",
            )
        )

    return rows


# -----------------------------------------------------------------------------
# Interactive HTML report
# -----------------------------------------------------------------------------

def severity_class(value: str) -> str:
    value = (value or "").lower()
    if value in {"ok", "low", "none"}:
        return "ok"
    if value in {"info"}:
        return "info"
    if value in {"review", "medium"}:
        return "review"
    if value in {"warning", "high"}:
        return "warning"
    if value in {"critical", "fail"}:
        return "critical"
    return "neutral"


def badge(value: str) -> str:
    cls = severity_class(value)
    return f'<span class="badge {cls}">{esc(value)}</span>'


def card(title: str, value: Any, subtitle: str = "", cls: str = "neutral") -> str:
    return f"""
    <div class="card {severity_class(cls)}">
      <div class="card-title">{esc(title)}</div>
      <div class="card-value">{esc(value)}</div>
      <div class="card-subtitle">{esc(subtitle)}</div>
    </div>
    """


def table_rows_for_details(detail_rows: List[SharingCompatibilityDetailRow]) -> str:
    html_rows = []
    for row in detail_rows:
        html_rows.append(f"""
        <tr data-severity="{esc(row.severity)}" data-status="{esc(row.sharing_status)}">
          <td>{esc(row.layer_title)}</td>
          <td>{esc(row.layer_visibility)}</td>
          <td>{esc(row.layer_access_mode_used)}</td>
          <td>{'Yes' if row.layer_endpoint_reachable else 'No'}</td>
          <td>{badge(row.layer_risk_level)}</td>
          <td>{esc(row.active_dependency_count)}</td>
          <td class="small">{esc(row.active_dependency_widgets)}</td>
          <td>{esc(row.sharing_status)}</td>
          <td>{badge(row.severity)}</td>
          <td class="small">{esc(row.recommendation)}</td>
        </tr>
        """)
    return "\n".join(html_rows)


def table_rows_for_recommendations(rows: List[SharingRecommendationRow]) -> str:
    html_rows = []
    for row in rows:
        html_rows.append(f"""
        <tr data-priority="{esc(row.priority)}">
          <td>{badge(row.priority)}</td>
          <td>{esc(row.affected_item_type)}</td>
          <td>{esc(row.affected_item_title)}</td>
          <td class="small">{esc(row.issue)}</td>
          <td class="small">{esc(row.recommendation)}</td>
          <td class="small">{esc(row.evidence)}</td>
        </tr>
        """)
    return "\n".join(html_rows)


def generate_diagnosis_text(summary: SharingCompatibilitySummaryRow) -> str:
    parts = [
        f"The app is shared as '{summary.app_access}'.",
        f"The referenced web map sharing level is '{summary.webmap_access_summary}'.",
        f"{summary.public_reachable_layer_count} of {summary.layer_count} layer(s) were reachable anonymously.",
        f"{summary.possible_broken_dependency_count} possible broken active dependency/dependencies were detected.",
    ]

    if summary.template_residue_dependency_count:
        parts.append(
            f"{summary.template_residue_dependency_count} likely template/copy residue reference(s) were found. "
            "This is treated as a maintenance note, not an operational failure."
        )

    if summary.overall_risk_level == "low":
        parts.append("No immediate sharing compatibility action is required based on the available scan outputs.")

    return " ".join(parts)


def write_html_report(
    path: Path,
    summary: SharingCompatibilitySummaryRow,
    detail_rows: List[SharingCompatibilityDetailRow],
    recommendation_rows: List[SharingRecommendationRow],
) -> None:
    generated_at = now_utc_string()
    diagnosis = generate_diagnosis_text(summary)

    html_content = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>LHI ExB App Inspector - Sharing Compatibility Report</title>
<meta name="viewport" content="width=device-width, initial-scale=1">
<style>
:root {{
  --bg: #f7f8fb;
  --panel: #ffffff;
  --text: #1f2937;
  --muted: #6b7280;
  --border: #e5e7eb;
  --ok: #047857;
  --ok-bg: #d1fae5;
  --info: #0369a1;
  --info-bg: #e0f2fe;
  --review: #92400e;
  --review-bg: #fef3c7;
  --warning: #b45309;
  --warning-bg: #ffedd5;
  --critical: #b91c1c;
  --critical-bg: #fee2e2;
  --neutral-bg: #f3f4f6;
}}
* {{ box-sizing: border-box; }}
body {{
  margin: 0;
  font-family: Segoe UI, Arial, sans-serif;
  background: var(--bg);
  color: var(--text);
}}
header {{
  background: linear-gradient(135deg, #111827, #374151);
  color: white;
  padding: 28px 34px;
}}
header h1 {{
  margin: 0 0 8px 0;
  font-size: 28px;
}}
header p {{
  margin: 0;
  color: #d1d5db;
}}
main {{
  padding: 24px 34px 44px;
}}
.section {{
  background: var(--panel);
  border: 1px solid var(--border);
  border-radius: 16px;
  padding: 20px;
  margin-bottom: 20px;
  box-shadow: 0 6px 18px rgba(0,0,0,0.04);
}}
.grid {{
  display: grid;
  grid-template-columns: repeat(6, minmax(150px, 1fr));
  gap: 14px;
}}
.card {{
  border-radius: 14px;
  padding: 16px;
  border: 1px solid var(--border);
  background: var(--neutral-bg);
}}
.card.ok {{ background: var(--ok-bg); }}
.card.info {{ background: var(--info-bg); }}
.card.review {{ background: var(--review-bg); }}
.card.warning {{ background: var(--warning-bg); }}
.card.critical {{ background: var(--critical-bg); }}
.card-title {{
  font-size: 12px;
  text-transform: uppercase;
  color: var(--muted);
  letter-spacing: .04em;
}}
.card-value {{
  font-size: 24px;
  font-weight: 700;
  margin-top: 4px;
}}
.card-subtitle {{
  font-size: 12px;
  color: var(--muted);
  margin-top: 4px;
}}
.badge {{
  display: inline-block;
  padding: 4px 9px;
  border-radius: 999px;
  font-size: 12px;
  font-weight: 700;
  white-space: nowrap;
}}
.badge.ok {{ background: var(--ok-bg); color: var(--ok); }}
.badge.info {{ background: var(--info-bg); color: var(--info); }}
.badge.review {{ background: var(--review-bg); color: var(--review); }}
.badge.warning {{ background: var(--warning-bg); color: var(--warning); }}
.badge.critical {{ background: var(--critical-bg); color: var(--critical); }}
.badge.neutral {{ background: var(--neutral-bg); color: var(--text); }}
.controls {{
  display: flex;
  gap: 10px;
  margin-bottom: 14px;
  flex-wrap: wrap;
}}
input, select {{
  padding: 10px 12px;
  border: 1px solid var(--border);
  border-radius: 10px;
  min-width: 220px;
}}
table {{
  width: 100%;
  border-collapse: collapse;
  font-size: 14px;
}}
th, td {{
  border-bottom: 1px solid var(--border);
  padding: 10px;
  vertical-align: top;
  text-align: left;
}}
th {{
  background: #f9fafb;
  position: sticky;
  top: 0;
  z-index: 1;
}}
.small {{
  font-size: 12px;
  color: #4b5563;
  max-width: 420px;
}}
.note {{
  line-height: 1.55;
  color: #374151;
}}
.footer {{
  color: var(--muted);
  font-size: 12px;
  margin-top: 18px;
}}
@media (max-width: 1200px) {{
  .grid {{ grid-template-columns: repeat(3, 1fr); }}
}}
@media (max-width: 760px) {{
  main {{ padding: 16px; }}
  .grid {{ grid-template-columns: 1fr; }}
  table {{ font-size: 12px; }}
}}
</style>
</head>
<body>
<header>
  <h1>LHI ExB App Inspector</h1>
  <p>Sharing Compatibility Report · Generated {esc(generated_at)}</p>
</header>

<main>
  <section class="section">
    <h2>{esc(summary.app_title)}</h2>
    <p class="note"><strong>App Item ID:</strong> {esc(summary.app_item_id)}</p>
    <p class="note">{esc(diagnosis)}</p>
  </section>

  <section class="section">
    <h2>Executive Summary</h2>
    <div class="grid">
      {card("Overall Risk", summary.overall_risk_level, summary.overall_status, summary.overall_risk_level)}
      {card("Operational Risk", summary.operational_risk_level, "Excludes template residue", summary.operational_risk_level)}
      {card("App Sharing", summary.app_access, "Experience Builder app", "neutral")}
      {card("Web Map Sharing", summary.webmap_access_summary, "Referenced web map(s)", "neutral")}
      {card("Public Layers", f"{summary.public_reachable_layer_count}/{summary.layer_count}", "Anonymous reachable", "ok" if summary.public_reachable_layer_count == summary.layer_count else "review")}
      {card("Broken Dependencies", summary.possible_broken_dependency_count, "Active dependency risk", "ok" if summary.possible_broken_dependency_count == 0 else "warning")}
      {card("Template Residue", summary.template_residue_dependency_count, "Maintenance note", "info" if summary.template_residue_dependency_count else "ok")}
      {card("Active Dependencies", summary.active_dependency_count, f"{summary.active_dependency_layer_count} active layer(s)", "neutral")}
    </div>
  </section>

  <section class="section">
    <h2>Layer Compatibility</h2>
    <div class="controls">
      <input type="text" id="layerSearch" placeholder="Search layers, widgets, status...">
      <select id="severityFilter">
        <option value="">All severities</option>
        <option value="ok">OK</option>
        <option value="review">Review</option>
        <option value="warning">Warning</option>
        <option value="high">High</option>
        <option value="critical">Critical</option>
      </select>
    </div>
    <table id="layerTable">
      <thead>
        <tr>
          <th>Layer</th>
          <th>Visible</th>
          <th>Access Mode</th>
          <th>Reachable</th>
          <th>Health Risk</th>
          <th>Active Deps</th>
          <th>Widgets</th>
          <th>Sharing Status</th>
          <th>Severity</th>
          <th>Recommendation</th>
        </tr>
      </thead>
      <tbody>
        {table_rows_for_details(detail_rows)}
      </tbody>
    </table>
  </section>

  <section class="section">
    <h2>Recommendations</h2>
    <table id="recommendationTable">
      <thead>
        <tr>
          <th>Priority</th>
          <th>Type</th>
          <th>Title</th>
          <th>Issue</th>
          <th>Recommendation</th>
          <th>Evidence</th>
        </tr>
      </thead>
      <tbody>
        {table_rows_for_recommendations(recommendation_rows)}
      </tbody>
    </table>
  </section>

  <section class="section">
    <h2>Interpretation Notes</h2>
    <p class="note"><strong>Template residue:</strong> copied or embedded data source references inherited from a reusable Experience Builder template. These are informational unless the related widgets are visible and failing.</p>
    <p class="note"><strong>Anonymous access:</strong> means the REST endpoint was reachable without a token. This is desirable for public-facing services and avoids false invalid-token failures from mixed AGOL/Enterprise setups.</p>
    <p class="note"><strong>Operational risk:</strong> reflects active sharing/layer problems. Maintenance notes do not automatically mean the app is broken.</p>
    <div class="footer">Lazy Hat Innovations · Build fast. Think deeply. Publish strategically.</div>
  </section>
</main>

<script>
function filterLayerTable() {{
  const search = document.getElementById('layerSearch').value.toLowerCase();
  const severity = document.getElementById('severityFilter').value.toLowerCase();
  const rows = document.querySelectorAll('#layerTable tbody tr');

  rows.forEach(row => {{
    const text = row.innerText.toLowerCase();
    const rowSeverity = (row.getAttribute('data-severity') || '').toLowerCase();
    const matchesSearch = !search || text.includes(search);
    const matchesSeverity = !severity || rowSeverity === severity;
    row.style.display = matchesSearch && matchesSeverity ? '' : 'none';
  }});
}}

document.getElementById('layerSearch').addEventListener('input', filterLayerTable);
document.getElementById('severityFilter').addEventListener('change', filterLayerTable);
</script>
</body>
</html>
"""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(html_content, encoding="utf-8")
    logging.info("HTML report written: %s", path)


# -----------------------------------------------------------------------------
# CLI
# -----------------------------------------------------------------------------

def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Check sharing compatibility across ExB app, web map, layer health, and dependency scan outputs."
    )
    parser.add_argument("--app-summary-csv", required=True, help="Path to app_summary_*.csv from Script 01.")
    parser.add_argument("--webmap-summary-csv", required=True, help="Path to webmap_summary_*.csv from Script 03.")
    parser.add_argument("--webmap-layers-csv", required=True, help="Path to webmap_layers_*.csv from Script 03.")
    parser.add_argument("--exb-resolution-csv", required=True, help="Path to exb_layer_reference_resolution_*.csv from Script 03.")
    parser.add_argument("--layer-health-details-csv", required=True, help="Path to layer_health_details_*.csv from Script 04.")
    parser.add_argument("--output-prefix", default=None, help="Optional output filename prefix. Defaults to sharing_check timestamp.")
    return parser.parse_args()


def main() -> int:
    log_path = setup_logging()
    args = parse_args()

    try:
        app_summary_path = Path(args.app_summary_csv)
        webmap_summary_path = Path(args.webmap_summary_csv)
        webmap_layers_path = Path(args.webmap_layers_csv)
        exb_resolution_path = Path(args.exb_resolution_csv)
        layer_health_details_path = Path(args.layer_health_details_csv)

        for path in [app_summary_path, webmap_summary_path, webmap_layers_path, exb_resolution_path, layer_health_details_path]:
            if not path.exists():
                raise FileNotFoundError(f"Required input file not found: {path}")

        timestamp = dt.now().strftime("%Y%m%d_%H%M%S")
        output_prefix = args.output_prefix or f"sharing_check_{timestamp}"

        logging.info("Starting LHI ExB App Inspector - Script 05")

        app_rows = read_csv_dicts(app_summary_path)
        webmap_summary_rows = read_csv_dicts(webmap_summary_path)
        webmap_layer_rows = read_csv_dicts(webmap_layers_path)
        resolution_rows = read_csv_dicts(exb_resolution_path)
        layer_health_rows = read_csv_dicts(layer_health_details_path)

        app_row = first_row(app_rows, "App summary CSV")

        detail_rows = make_detail_rows(
            app_row=app_row,
            webmap_summary_rows=webmap_summary_rows,
            webmap_layer_rows=webmap_layer_rows,
            resolution_rows=resolution_rows,
            layer_health_rows=layer_health_rows,
        )

        summary_row = make_summary_row(
            app_row=app_row,
            webmap_summary_rows=webmap_summary_rows,
            detail_rows=detail_rows,
            resolution_rows=resolution_rows,
        )

        recommendation_rows = make_recommendations(summary_row, detail_rows)

        summary_csv = CSV_DIR / f"sharing_compatibility_summary_{output_prefix}_{timestamp}.csv"
        details_csv = CSV_DIR / f"sharing_compatibility_details_{output_prefix}_{timestamp}.csv"
        recommendations_csv = CSV_DIR / f"sharing_compatibility_recommendations_{output_prefix}_{timestamp}.csv"
        report_html = REPORT_DIR / f"sharing_compatibility_report_{output_prefix}_{timestamp}.html"

        write_csv(summary_csv, [summary_row])
        write_csv(details_csv, detail_rows)
        write_csv(recommendations_csv, recommendation_rows)
        write_html_report(report_html, summary_row, detail_rows, recommendation_rows)

        print("\n=== LHI ExB App Inspector: Script 05 Complete ===")
        print(f"App: {summary_row.app_title}")
        print(f"App access: {summary_row.app_access}")
        print(f"Web map access summary: {summary_row.webmap_access_summary}")
        print(f"Layers checked: {summary_row.layer_count}")
        print(f"Public reachable layers: {summary_row.public_reachable_layer_count}")
        print(f"Authenticated layers: {summary_row.authenticated_layer_count}")
        print(f"Inaccessible layers: {summary_row.inaccessible_layer_count}")
        print(f"Active dependency count: {summary_row.active_dependency_count}")
        print(f"Active dependency layer count: {summary_row.active_dependency_layer_count}")
        print(f"Template residue references: {summary_row.template_residue_dependency_count}")
        print(f"Possible broken dependencies: {summary_row.possible_broken_dependency_count}")
        print(f"Internal/dev/test service layers: {summary_row.internal_service_layer_count}")
        print(f"Internal/dev/test service layers unreachable: {summary_row.internal_service_unreachable_count}")
        print(f"Overall status: {summary_row.overall_status}")
        print(f"Overall risk: {summary_row.overall_risk_level}")
        print(f"Operational risk: {summary_row.operational_risk_level}")
        print(f"Overall risk score: {summary_row.overall_risk_score}")
        print("\nOutputs:")
        print(f"Sharing summary CSV: {summary_csv}")
        print(f"Sharing details CSV: {details_csv}")
        print(f"Sharing recommendations CSV: {recommendations_csv}")
        print(f"Interactive HTML report: {report_html}")
        print(f"Log file: {log_path}")

        if summary_row.issue_summary:
            print("\nSummary:")
            print(summary_row.issue_summary)

        return 0

    except Exception as exc:
        logging.exception("Sharing compatibility check failed: %s", exc)
        print("\nSharing compatibility check failed. Check the log file for details:")
        print(log_path)
        print(f"Error: {exc}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
