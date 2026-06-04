"""
LHI ExB App Inspector
Script 09: Org-wide Experience Builder App Discovery

Purpose:
- Search an ArcGIS Online / Portal organization for Experience Builder apps
- Produce an input CSV that Script 07 can use directly
- Produce a richer discovery inventory CSV for review, filtering, and governance
- Help move from manual app IDs to org-wide ExB portfolio scanning

v0.2.0 change:
- Uses the portal REST search endpoint through the authenticated GIS connection.
- Avoids hydrated Item objects to prevent slow/noisy repeated role lookups.
- Applies orgid filtering by default so discovery stays inside the connected organization.
- Adds discovery modes: standard, broad, exhaustive.
- Can write excluded/weak candidates for review.
- Extracts Experience Builder status/version/template metadata and supports cleaner filters.

Outputs:
- outputs/csv/discovered_exb_apps_input_*.csv
- outputs/csv/discovered_exb_apps_inventory_*.csv
- outputs/csv/discovered_exb_apps_summary_*.csv

Author: Lazy Hat Innovations
Version: 0.4.2
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
from typing import Any, Dict, List, Optional, Set, Tuple

try:
    from arcgis.gis import GIS
except ImportError:
    GIS = None


SCRIPT_VERSION = "0.4.2"
OUTPUT_ROOT = Path("outputs")
CSV_DIR = OUTPUT_ROOT / "csv"
LOG_DIR = OUTPUT_ROOT / "logs"
DEFAULT_PORTAL = "https://www.arcgis.com"


@dataclass
class DiscoveredExBAppRow:
    scan_timestamp_utc: str
    app_item_id: str
    app_title: str
    app_type: str
    owner: str
    access: str
    created_utc: str
    modified_utc: str
    num_views: str
    size: str
    tags: str
    categories: str
    snippet: str
    description_present: bool
    item_url: str
    app_launch_url: str
    is_public: bool
    is_org_shared: bool
    is_private: bool
    is_authoritative: bool
    content_status: str
    type_keywords: str
    exb_status: str
    exb_version: str
    publish_version: str
    template_id: str
    is_template: bool
    is_published: bool
    is_draft: bool
    discovery_method: str
    likely_experience_builder: bool
    review_note: str


@dataclass
class DiscoverySummaryRow:
    scan_timestamp_utc: str
    portal: str
    query_used: str
    total_items_found_raw: int
    total_unique_items_seen: int
    total_exb_apps_discovered: int
    public_count: int
    org_count: int
    private_count: int
    authoritative_count: int
    web_experience_count: int
    web_experience_template_count: int
    published_count: int
    draft_count: int
    changed_count: int
    template_count: int
    owner_count: int
    newest_modified_utc: str
    oldest_modified_utc: str
    output_input_csv: str
    output_inventory_csv: str
    output_excluded_candidates_csv: str
    issue_summary: str


def setup_output_dirs() -> None:
    for folder in [OUTPUT_ROOT, CSV_DIR, LOG_DIR]:
        folder.mkdir(parents=True, exist_ok=True)


def setup_logging() -> Path:
    setup_output_dirs()
    timestamp = dt.now().strftime("%Y%m%d_%H%M%S")
    log_path = LOG_DIR / f"discover_exb_apps_{timestamp}.log"

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


def clean_text(value: Any, max_len: int = 500) -> str:
    text = safe_str(value).replace("\r", " ").replace("\n", " ").strip()
    text = re.sub(r"\s+", " ", text)
    if len(text) > max_len:
        return text[:max_len - 3] + "..."
    return text


def join_list(value: Any) -> str:
    if not value:
        return ""
    if isinstance(value, list):
        return "; ".join(clean_text(v, 120) for v in value if clean_text(v, 120))
    return clean_text(value, 300)


def format_epoch_ms(value: Any) -> str:
    try:
        if value in [None, ""]:
            return ""
        seconds = float(value) / 1000.0
        return dt.fromtimestamp(seconds, tz=timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
    except Exception:
        return safe_str(value)


def parse_owner_filter(value: Optional[str]) -> Set[str]:
    if not value:
        return set()
    return {v.strip().lower() for v in value.split(",") if v.strip()}


def parse_access_filter(value: Optional[str]) -> Set[str]:
    if not value:
        return set()
    allowed = {"public", "org", "private", "shared"}
    result = {v.strip().lower() for v in value.split(",") if v.strip()}
    invalid = result - allowed
    if invalid:
        raise ValueError(f"Invalid access filter values: {', '.join(sorted(invalid))}. Allowed: public, org, private, shared")
    return result


def write_csv(path: Path, rows: List[Any]) -> None:
    if not rows:
        logging.warning("No rows to write for %s", path)
        return

    with path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=list(asdict(rows[0]).keys()))
        writer.writeheader()
        for row in rows:
            writer.writerow(asdict(row))

    logging.info("CSV written: %s | rows: %s", path, len(rows))


def write_input_apps_csv(path: Path, rows: List[DiscoveredExBAppRow]) -> None:
    with path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=["app_item_id", "app_name", "notes"])
        writer.writeheader()
        for row in rows:
            writer.writerow({
                "app_item_id": row.app_item_id,
                "app_name": row.app_title,
                "notes": f"Discovered by Script 09 | owner={row.owner} | access={row.access}",
            })

    logging.info("Script 07 input CSV written: %s | rows: %s", path, len(rows))


def connect_to_portal(portal_url: str, username: Optional[str], anonymous: bool) -> Any:
    if GIS is None:
        raise ImportError("The arcgis package is not installed. Install it with: pip install arcgis")

    if anonymous:
        logging.info("Connecting anonymously to portal: %s", portal_url)
        return GIS(portal_url)

    if not username:
        username = input("ArcGIS username: ").strip()

    password = os.getenv("LHI_ARCGIS_PASSWORD")
    if not password:
        password = getpass.getpass(f"Password for {username}: ")

    logging.info("Connecting to portal: %s as %s", portal_url, username)
    return GIS(portal_url, username, password)


def get_connected_org_id(gis: Any) -> str:
    """
    Attempts to read the connected ArcGIS organization ID.
    Required to prevent portal search from returning global ArcGIS Online content.
    """
    try:
        props = getattr(gis, "properties", None)
        if props:
            # ArcGIS PropertyMap usually supports attribute and dict-style access.
            org_id = getattr(props, "id", None)
            if org_id:
                return str(org_id)
            try:
                org_id = props.get("id")
                if org_id:
                    return str(org_id)
            except Exception:
                pass
    except Exception:
        pass

    try:
        portal = getattr(gis, "_portal", None)
        if portal:
            props = getattr(portal, "properties", None)
            if props:
                org_id = getattr(props, "id", None)
                if org_id:
                    return str(org_id)
                try:
                    org_id = props.get("id")
                    if org_id:
                        return str(org_id)
                except Exception:
                    pass
    except Exception:
        pass

    return ""


def append_org_filter(query: str, org_id: str, include_outside_org: bool) -> str:
    """
    ArcGIS Online search is global unless constrained.
    Adding orgid:<id> keeps discovery inside the connected organization.
    """
    if include_outside_org or not org_id:
        return query
    if "orgid:" in query.lower():
        return query
    return f"{query} orgid:{org_id}"



def build_search_queries(args: argparse.Namespace, org_id: str = "") -> List[Tuple[str, str]]:
    """
    Builds REST search query patterns.

    Modes:
    - standard: conservative Web Experience / Experience Builder signals.
    - broad: adds Web Mapping Application + ExB/Experience keywords.
    - exhaustive: adds broader app-like item searches; client-side filtering still applies.
    """
    owner_filter = ""
    if args.owner:
        owners = [o.strip() for o in args.owner.split(",") if o.strip()]
        if len(owners) == 1:
            owner_filter = f" owner:{owners[0]}"

    search_suffix = f" {args.search}" if args.search else ""

    standard_queries = [
        ("type_web_experience", f'type:"Web Experience"{owner_filter}{search_suffix}'),
        ("type_keywords_experience_builder", f'typekeywords:"Experience Builder"{owner_filter}{search_suffix}'),
        ("type_keywords_web_experience", f'typekeywords:"Web Experience"{owner_filter}{search_suffix}'),
        ("keyword_experience_builder", f'"Experience Builder"{owner_filter}{search_suffix}'),
    ]

    broad_queries = [
        ("wma_typekeywords_exb", f'type:"Web Mapping Application" typekeywords:EXB{owner_filter}{search_suffix}'),
        ("wma_typekeywords_experience_builder", f'type:"Web Mapping Application" typekeywords:"Experience Builder"{owner_filter}{search_suffix}'),
        ("typekeywords_exb", f'typekeywords:EXB{owner_filter}{search_suffix}'),
        ("typekeywords_experience", f'typekeywords:experience{owner_filter}{search_suffix}'),
        ("typekeywords_webappbuilder_experience", f'typekeywords:"Web AppBuilder" experience{owner_filter}{search_suffix}'),
    ]

    exhaustive_queries = [
        ("all_web_mapping_apps", f'type:"Web Mapping Application"{owner_filter}{search_suffix}'),
        ("all_application_items", f'type:Application{owner_filter}{search_suffix}'),
        ("apps_keyword_experience", f'experience app{owner_filter}{search_suffix}'),
        ("apps_keyword_builder", f'builder app{owner_filter}{search_suffix}'),
    ]

    mode = (args.mode or "standard").lower()
    if mode == "standard":
        queries = standard_queries
    elif mode == "broad":
        queries = standard_queries + broad_queries
    elif mode == "exhaustive":
        queries = standard_queries + broad_queries + exhaustive_queries
    else:
        raise ValueError(f"Unsupported discovery mode: {args.mode}")

    queries = [
        (method, append_org_filter(query, org_id=org_id, include_outside_org=args.include_outside_org))
        for method, query in queries
    ]

    return queries


def raw_portal_search(gis: Any, query: str, max_total: int, page_size: int) -> Tuple[List[Dict[str, Any]], int]:
    """
    Uses the portal sharing/rest search endpoint through the GIS connection.
    This avoids creating hydrated Item objects, which can be slow for large org searches.
    """
    page_size = max(1, min(page_size, 100))
    max_total = max(1, max_total)

    results: List[Dict[str, Any]] = []
    start = 1
    raw_total = 0

    while len(results) < max_total:
        num = min(page_size, max_total - len(results))
        params = {
            "q": query,
            "f": "json",
            "num": num,
            "start": start,
            "sortField": "modified",
            "sortOrder": "desc",
        }

        logging.info("REST search: start=%s num=%s query=%s", start, num, query)
        response = gis._con.get("search", params)

        if not isinstance(response, dict):
            logging.warning("Unexpected search response type for query [%s]: %s", query, type(response))
            break

        raw_total = max(raw_total, int(response.get("total", 0) or 0))
        page_results = response.get("results", []) or []

        if not page_results:
            break

        results.extend(page_results)

        next_start = response.get("nextStart", -1)
        if next_start in [-1, 0, None] or int(next_start) <= start:
            break

        start = int(next_start)

    return results, raw_total


def content_status(item: Dict[str, Any]) -> str:
    return safe_str(item.get("contentStatus") or item.get("content_status") or "")


def is_authoritative(item: Dict[str, Any]) -> bool:
    status = content_status(item).lower()
    if "authoritative" in status:
        return True

    props = item.get("properties") or {}
    if isinstance(props, dict) and str(props.get("isAuthoritative", "")).lower() in {"true", "1", "yes"}:
        return True

    return False


def is_likely_exb_item(item: Dict[str, Any]) -> Tuple[bool, str]:
    """
    Detects likely Experience Builder apps from raw item metadata.

    This intentionally supports strong and moderate signals because older or copied
    ExB apps may not all have identical item metadata.
    """
    item_type = safe_str(item.get("type", ""))
    type_keywords = [str(k).lower() for k in (item.get("typeKeywords") or item.get("typekeywords") or [])]
    title = safe_str(item.get("title", ""))
    url = safe_str(item.get("url", ""))
    snippet = safe_str(item.get("snippet", ""))
    description = safe_str(item.get("description", ""))

    signals = []
    score = 0

    keyword_blob = " ".join(type_keywords)
    text_blob = " ".join([title, url, snippet, description, keyword_blob]).lower()

    # Strong signals
    if item_type.lower() == "web experience":
        signals.append("type=Web Experience")
        score += 100

    if "experience builder" in keyword_blob:
        signals.append("typeKeywords include Experience Builder")
        score += 80

    if "web experience" in keyword_blob:
        signals.append("typeKeywords include Web Experience")
        score += 70

    if "exb" in keyword_blob:
        signals.append("typeKeywords include EXB")
        score += 70

    if "experiencebuilder" in text_blob:
        signals.append("metadata references experiencebuilder")
        score += 60

    # Moderate signals
    if item_type.lower() == "web mapping application" and "experience" in keyword_blob:
        signals.append("Web Mapping Application with experience keyword")
        score += 45

    if item_type.lower() == "web mapping application" and "exb" in text_blob:
        signals.append("Web Mapping Application with EXB reference")
        score += 45

    if "experience builder" in title.lower():
        signals.append("title contains Experience Builder")
        score += 35

    if "web experience" in text_blob:
        signals.append("metadata references Web Experience")
        score += 35

    url_lower = url.lower()
    if "experience" in url_lower and ("apps" in url_lower or "experiencebuilder" in url_lower):
        signals.append("URL resembles Experience Builder app")
        score += 40

    # Weak hints useful only when include-weak-matches is enabled.
    if "experience" in text_blob:
        signals.append("weak metadata contains experience")
        score += 10

    if "builder" in text_blob:
        signals.append("weak metadata contains builder")
        score += 5

    likely = score >= 40
    return likely, "; ".join(signals)


def parse_exb_metadata(item: Dict[str, Any]) -> Dict[str, Any]:
    """
    Extracts useful Experience Builder metadata from typeKeywords and related fields.

    Common examples:
    - status: Published
    - status: Draft
    - version:1.20.0
    - publishVersion:1.20.0
    - template:blankfullscreen
    """
    keywords = item.get("typeKeywords") or item.get("typekeywords") or []
    keyword_texts = [str(k).strip() for k in keywords if str(k).strip()]
    lower_keywords = [k.lower() for k in keyword_texts]

    result = {
        "exb_status": "",
        "exb_version": "",
        "publish_version": "",
        "template_id": "",
        "is_template": False,
        "is_published": False,
        "is_draft": False,
    }

    item_type = safe_str(item.get("type", ""))
    if item_type.lower() == "web experience template":
        result["is_template"] = True

    for raw, lower in zip(keyword_texts, lower_keywords):
        compact = lower.replace(" ", "")

        if lower.startswith("status:"):
            result["exb_status"] = raw.split(":", 1)[1].strip()
        elif lower in {"published", "status published", "status: published"}:
            result["exb_status"] = result["exb_status"] or "Published"
        elif lower in {"draft", "status draft", "status: draft"}:
            result["exb_status"] = result["exb_status"] or "Draft"
        elif lower in {"changed", "status changed", "status: changed"}:
            result["exb_status"] = result["exb_status"] or "Changed"

        if lower.startswith("version:"):
            result["exb_version"] = raw.split(":", 1)[1].strip()
        elif lower.startswith("publishversion:") or compact.startswith("publishversion:"):
            result["publish_version"] = raw.split(":", 1)[1].strip()
        elif lower.startswith("publish version:"):
            result["publish_version"] = raw.split(":", 1)[1].strip()

        if lower.startswith("template:"):
            result["template_id"] = raw.split(":", 1)[1].strip()
        elif lower.startswith("template "):
            result["template_id"] = raw.split(" ", 1)[1].strip()
        elif lower == "template" or "web experience template" in lower:
            result["is_template"] = True

    status_lower = result["exb_status"].lower()
    result["is_published"] = status_lower == "published"
    result["is_draft"] = status_lower == "draft"

    if not result["exb_status"]:
        if any(k == "draft" or "status: draft" in k for k in lower_keywords):
            result["exb_status"] = "Draft"
            result["is_draft"] = True
        elif any(k == "published" or "status: published" in k for k in lower_keywords):
            result["exb_status"] = "Published"
            result["is_published"] = True
        elif any(k == "changed" or "status: changed" in k for k in lower_keywords):
            result["exb_status"] = "Changed"

    return result



def build_inventory_row(item: Dict[str, Any], method: str) -> DiscoveredExBAppRow:
    likely, note = is_likely_exb_item(item)

    access = safe_str(item.get("access", "")).lower()
    tags = join_list(item.get("tags", []))
    categories = join_list(item.get("categories", []))
    snippet = clean_text(item.get("snippet", ""), 300)
    description = clean_text(item.get("description", ""), 300)
    item_url = safe_str(item.get("url", ""))
    exb_meta = parse_exb_metadata(item)

    return DiscoveredExBAppRow(
        scan_timestamp_utc=now_utc_string(),
        app_item_id=safe_str(item.get("id", "")),
        app_title=safe_str(item.get("title", "")),
        app_type=safe_str(item.get("type", "")),
        owner=safe_str(item.get("owner", "")),
        access=access,
        created_utc=format_epoch_ms(item.get("created", "")),
        modified_utc=format_epoch_ms(item.get("modified", "")),
        num_views=safe_str(item.get("numViews", "")),
        size=safe_str(item.get("size", "")),
        tags=tags,
        categories=categories,
        snippet=snippet,
        description_present=bool(description),
        item_url=item_url,
        app_launch_url=item_url,
        is_public=access == "public",
        is_org_shared=access == "org",
        is_private=access in {"private", "shared"} or access == "",
        is_authoritative=is_authoritative(item),
        content_status=content_status(item),
        type_keywords=join_list(item.get("typeKeywords", [])),
        exb_status=exb_meta["exb_status"],
        exb_version=exb_meta["exb_version"],
        publish_version=exb_meta["publish_version"],
        template_id=exb_meta["template_id"],
        is_template=exb_meta["is_template"],
        is_published=exb_meta["is_published"],
        is_draft=exb_meta["is_draft"],
        discovery_method=method,
        likely_experience_builder=likely,
        review_note=note or "Weak/no explicit ExB signal. Review item manually if included.",
    )


def filter_rows(rows: List[DiscoveredExBAppRow], args: argparse.Namespace) -> List[DiscoveredExBAppRow]:
    owner_filter = parse_owner_filter(args.owner)
    access_filter = parse_access_filter(args.access)

    result = []
    for row in rows:
        if owner_filter and row.owner.lower() not in owner_filter:
            continue

        if access_filter and row.access.lower() not in access_filter:
            continue

        if args.only_likely and not row.likely_experience_builder:
            continue

        item_type_filter = (args.item_type or "all").lower()
        if item_type_filter == "web-experience-only" and row.app_type.lower() != "web experience":
            continue
        if item_type_filter == "templates-only" and row.app_type.lower() != "web experience template":
            continue

        status_filter = (args.status or "all").lower()
        row_status = (row.exb_status or "").strip().lower()

        if status_filter == "published" and row_status != "published":
            continue
        if status_filter == "draft" and row_status != "draft":
            continue
        if status_filter == "changed" and row_status != "changed":
            continue
        if status_filter == "published-or-changed" and row_status not in {"published", "changed"}:
            continue
        if status_filter == "unknown" and row.exb_status:
            continue

        if args.exclude_templates and (row.is_template or row.app_type.lower() == "web experience template"):
            continue

        if args.exclude_public and row.is_public:
            continue

        if args.exclude_private and row.is_private:
            continue

        result.append(row)

    return result


def discover_exb_apps(gis: Any, args: argparse.Namespace) -> Tuple[List[DiscoveredExBAppRow], List[DiscoveredExBAppRow], int, int, str]:
    org_id = get_connected_org_id(gis)
    if org_id and not args.include_outside_org:
        logging.info("Restricting discovery to connected organization ID: %s", org_id)
    elif not org_id and not args.include_outside_org:
        logging.warning("Could not determine connected org ID. Search may return global ArcGIS Online content. Use --owner or --search to narrow results.")

    queries = build_search_queries(args, org_id=org_id)
    seen_ids: Set[str] = set()
    rows: List[DiscoveredExBAppRow] = []
    excluded_rows: List[DiscoveredExBAppRow] = []
    raw_found_total = 0
    raw_unique_seen = 0
    query_log = []

    for method, query in queries:
        query_log.append(f"{method}: {query}")
        raw_items, raw_total = raw_portal_search(
            gis=gis,
            query=query,
            max_total=args.max_total_per_query,
            page_size=args.page_size,
        )
        raw_found_total += raw_total

        for item in raw_items:
            item_id = safe_str(item.get("id", ""))
            if not item_id or item_id in seen_ids:
                continue

            seen_ids.add(item_id)
            raw_unique_seen += 1

            likely, _ = is_likely_exb_item(item)
            row = build_inventory_row(item, method)

            if not likely and not args.include_weak_matches:
                excluded_rows.append(row)
                continue

            rows.append(row)

    rows = filter_rows(rows, args)
    rows.sort(key=lambda r: r.modified_utc or "", reverse=True)
    excluded_rows.sort(key=lambda r: r.modified_utc or "", reverse=True)

    return rows, excluded_rows, raw_found_total, raw_unique_seen, " | ".join(query_log)


def build_summary(
    portal: str,
    query_used: str,
    raw_count: int,
    unique_count: int,
    rows: List[DiscoveredExBAppRow],
    input_csv: Path,
    inventory_csv: Path,
    excluded_csv: Path,
) -> DiscoverySummaryRow:
    owners = {row.owner for row in rows if row.owner}
    modified_dates = [row.modified_utc for row in rows if row.modified_utc]

    if rows:
        issue_summary = f"Discovered {len(rows)} likely Experience Builder app(s) across {len(owners)} owner(s)."
    else:
        issue_summary = "No Experience Builder apps discovered. Try --include-weak-matches, increase --max-total-per-query, or narrow by owner/search."

    public_count = sum(1 for row in rows if row.is_public)
    org_count = sum(1 for row in rows if row.is_org_shared)
    private_count = sum(1 for row in rows if row.is_private)
    authoritative_count = sum(1 for row in rows if row.is_authoritative)
    web_experience_count = sum(1 for row in rows if row.app_type.lower() == "web experience")
    web_experience_template_count = sum(1 for row in rows if row.app_type.lower() == "web experience template")
    published_count = sum(1 for row in rows if (row.exb_status or "").strip().lower() == "published")
    draft_count = sum(1 for row in rows if (row.exb_status or "").strip().lower() == "draft")
    changed_count = sum(1 for row in rows if (row.exb_status or "").strip().lower() == "changed")
    template_count = sum(1 for row in rows if row.is_template or row.app_type.lower() == "web experience template")

    return DiscoverySummaryRow(
        scan_timestamp_utc=now_utc_string(),
        portal=portal,
        query_used=query_used,
        total_items_found_raw=raw_count,
        total_unique_items_seen=unique_count,
        total_exb_apps_discovered=len(rows),
        public_count=public_count,
        org_count=org_count,
        private_count=private_count,
        authoritative_count=authoritative_count,
        web_experience_count=web_experience_count,
        web_experience_template_count=web_experience_template_count,
        published_count=published_count,
        draft_count=draft_count,
        changed_count=changed_count,
        template_count=template_count,
        owner_count=len(owners),
        newest_modified_utc=max(modified_dates) if modified_dates else "",
        oldest_modified_utc=min(modified_dates) if modified_dates else "",
        output_input_csv=str(input_csv),
        output_inventory_csv=str(inventory_csv),
        output_excluded_candidates_csv=str(excluded_csv),
        issue_summary=issue_summary,
    )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Discover Experience Builder apps in an ArcGIS organization and create Script 07 input CSV."
    )
    parser.add_argument("--portal", default=DEFAULT_PORTAL, help="Portal URL.")
    parser.add_argument("--username", default=None, help="Portal username. If omitted and --anonymous is not used, the script asks once.")
    parser.add_argument("--anonymous", action="store_true", help="Search anonymously. Only discovers public items.")
    parser.add_argument(
        "--include-outside-org",
        action="store_true",
        help="Do not apply orgid filtering. This can return global ArcGIS Online public content and is not recommended for normal org discovery.",
    )
    parser.add_argument(
        "--mode",
        choices=["standard", "broad", "exhaustive"],
        default="standard",
        help="Discovery mode. standard is conservative; broad adds more ExB metadata patterns; exhaustive searches broader app-like content.",
    )
    parser.add_argument(
        "--write-excluded-candidates",
        action="store_true",
        help="Write weak/excluded candidates to a separate CSV for review.",
    )
    parser.add_argument(
        "--item-type",
        choices=["all", "web-experience-only", "templates-only"],
        default="all",
        help="Filter discovered items by item type before writing Script 07 input CSV.",
    )
    parser.add_argument(
        "--status",
        choices=["all", "published", "draft", "changed", "published-or-changed", "unknown"],
        default="all",
        help="Filter by parsed Experience Builder status before writing Script 07 input CSV.",
    )
    parser.add_argument(
        "--exclude-templates",
        action="store_true",
        help="Exclude Web Experience Template items and template-marked items from output.",
    )
    parser.add_argument("--max-total-per-query", type=int, default=500, help="Maximum raw items to retrieve per query pattern. Default: 500.")
    parser.add_argument("--page-size", type=int, default=100, help="REST search page size. Max 100. Default: 100.")
    parser.add_argument("--search", default=None, help="Optional extra search term to narrow discovery.")
    parser.add_argument("--owner", default=None, help="Optional comma-separated owner filter. Example: user1,user2")
    parser.add_argument("--access", default=None, help="Optional comma-separated access filter: public,org,private,shared")
    parser.add_argument("--exclude-public", action="store_true", help="Exclude public apps from output.")
    parser.add_argument("--exclude-private", action="store_true", help="Exclude private/shared apps from output.")
    parser.add_argument("--include-weak-matches", action="store_true", help="Include weaker matches that do not have strong ExB metadata signals.")
    parser.add_argument("--only-likely", action="store_true", help="Keep only strong Experience Builder signals.")
    parser.add_argument(
        "--limit",
        type=int,
        default=None,
        help="Optional cap on the number of discovered apps written to the inventory and Script 07 input CSV. Useful for test batches.",
    )
    parser.add_argument("--output-prefix", default=None, help="Optional output filename prefix.")
    return parser.parse_args()


def main() -> int:
    log_path = setup_logging()
    args = parse_args()

    try:
        logging.info("Starting LHI ExB App Inspector - Script 09 v%s", SCRIPT_VERSION)
        logging.info("Portal: %s", args.portal)

        gis = connect_to_portal(args.portal, args.username, args.anonymous)
        org_id = get_connected_org_id(gis)
        if org_id:
            logging.info("Connected org ID: %s", org_id)
            print(f"Connected org ID: {org_id}")
        elif not args.include_outside_org:
            logging.warning("Connected org ID could not be detected. Discovery may not be org-scoped.")

        timestamp = dt.now().strftime("%Y%m%d_%H%M%S")
        prefix = args.output_prefix or f"exb_discovery_{timestamp}"

        rows, excluded_rows, raw_count, unique_count, query_used = discover_exb_apps(gis, args)

        pre_limit_count = len(rows)
        if args.limit is not None:
            if args.limit < 1:
                raise ValueError("--limit must be greater than 0.")
            rows = rows[:args.limit]
            logging.info("Applied --limit %s: %s rows reduced to %s rows.", args.limit, pre_limit_count, len(rows))

        input_csv = CSV_DIR / f"discovered_exb_apps_input_{prefix}_{timestamp}.csv"
        inventory_csv = CSV_DIR / f"discovered_exb_apps_inventory_{prefix}_{timestamp}.csv"
        excluded_csv = CSV_DIR / f"discovered_exb_apps_excluded_candidates_{prefix}_{timestamp}.csv"
        summary_csv = CSV_DIR / f"discovered_exb_apps_summary_{prefix}_{timestamp}.csv"

        write_input_apps_csv(input_csv, rows)
        write_csv(inventory_csv, rows)
        if args.write_excluded_candidates:
            write_csv(excluded_csv, excluded_rows)
        summary = build_summary(args.portal, query_used, raw_count, unique_count, rows, input_csv, inventory_csv, excluded_csv)
        write_csv(summary_csv, [summary])

        print(f"\n=== LHI ExB App Inspector: Script 09 v{SCRIPT_VERSION} Complete ===")
        print(f"Raw search item total across queries: {raw_count}")
        print(f"Unique raw items seen: {unique_count}")
        print(f"Discovery mode: {args.mode}")
        if args.limit is not None:
            print(f"Likely ExB apps before limit: {pre_limit_count}")
            print(f"Limit applied: {args.limit}")
        print(f"Likely ExB apps written: {len(rows)}")
        print(f"Excluded/weak candidates: {len(excluded_rows)}")
        print(f"Web Experience items: {summary.web_experience_count}")
        print(f"Web Experience Template items: {summary.web_experience_template_count}")
        print(f"Published apps: {summary.published_count}")
        print(f"Draft apps: {summary.draft_count}")
        print(f"Changed apps: {summary.changed_count}")
        print(f"Template-marked items: {summary.template_count}")
        print(f"Public apps: {summary.public_count}")
        print(f"Org-shared apps: {summary.org_count}")
        print(f"Private/shared apps: {summary.private_count}")
        print(f"Owners represented: {summary.owner_count}")
        print("\nOutputs:")
        print(f"Script 07 input CSV: {input_csv}")
        print(f"Discovery inventory CSV: {inventory_csv}")
        if args.write_excluded_candidates:
            print(f"Excluded candidates CSV: {excluded_csv}")
        print(f"Discovery summary CSV: {summary_csv}")
        print(f"Log file: {log_path}")
        print("\nNext step:")
        print(f"python 07_run_multi_exb_inspection.py --portal \"{args.portal}\" --apps-csv \"{input_csv}\"")
        print("\nSummary:")
        print(summary.issue_summary)

        return 0

    except KeyboardInterrupt:
        logging.warning("Discovery interrupted by user.")
        print("\nDiscovery interrupted by user. Partial outputs are not written.")
        print(f"Log file: {log_path}")
        return 130

    except Exception as exc:
        logging.exception("Experience Builder app discovery failed: %s", exc)
        print("\nExperience Builder app discovery failed. Check the log file for details:")
        print(log_path)
        print(f"Error: {exc}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
