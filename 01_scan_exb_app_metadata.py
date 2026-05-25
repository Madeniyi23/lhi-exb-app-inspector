"""
LHI ExB App Inspector
Script 01: Scan Experience Builder App Metadata

Purpose:
- Connect to ArcGIS Online or ArcGIS Enterprise
- Fetch one Experience Builder app item by URL or Item ID
- Export app metadata to CSV
- Save the raw app item data/config JSON for later dependency inspection
- Create a scan log for troubleshooting

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
from typing import Any, Dict, Optional, Tuple

try:
    from arcgis.gis import GIS
except ImportError:
    GIS = None


# -----------------------------------------------------------------------------
# Defaults
# -----------------------------------------------------------------------------

DEFAULT_PORTAL = "https://www.arcgis.com"
OUTPUT_ROOT = Path("outputs")
RAW_JSON_DIR = OUTPUT_ROOT / "raw_json"
CSV_DIR = OUTPUT_ROOT / "csv"
LOG_DIR = OUTPUT_ROOT / "logs"


# -----------------------------------------------------------------------------
# Data models
# -----------------------------------------------------------------------------

@dataclass
class AppMetadata:
    scan_timestamp_utc: str
    portal_url: str
    app_item_id: str
    title: str
    item_type: str
    owner: str
    created_utc: str
    modified_utc: str
    access: str
    is_public: bool
    is_org_shared: bool
    group_count: int
    groups: str
    num_views: Optional[int]
    url: str
    homepage: str
    size: Optional[int]
    type_keywords: str
    tags: str
    description_present: bool
    snippet_present: bool
    license_info_present: bool
    item_data_readable: bool
    item_data_top_level_keys: str
    possible_exb_config: bool
    raw_json_path: str
    issue_summary: str


# -----------------------------------------------------------------------------
# Utilities
# -----------------------------------------------------------------------------

def setup_output_dirs() -> None:
    for folder in [OUTPUT_ROOT, RAW_JSON_DIR, CSV_DIR, LOG_DIR]:
        folder.mkdir(parents=True, exist_ok=True)


def setup_logging() -> Path:
    setup_output_dirs()
    timestamp = dt.now().strftime("%Y%m%d_%H%M%S")
    log_path = LOG_DIR / f"scan_exb_app_metadata_{timestamp}.log"

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s | %(levelname)s | %(message)s",
        handlers=[
            logging.FileHandler(log_path, encoding="utf-8"),
            logging.StreamHandler(sys.stdout),
        ],
    )
    return log_path


def utc_from_esri_millis(value: Optional[int]) -> str:
    if value is None:
        return ""
    try:
        return dt.fromtimestamp(value / 1000, tz=timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
    except Exception:
        return ""


def now_utc_string() -> str:
    return dt.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")


def safe_join(values: Any) -> str:
    if not values:
        return ""
    if isinstance(values, list):
        return "; ".join(str(v) for v in values)
    return str(values)


def sanitize_filename(value: str) -> str:
    value = re.sub(r"[^A-Za-z0-9_\-]+", "_", value.strip())
    return value[:120] if value else "untitled"


def extract_item_id(value: str) -> str:
    """
    Accepts either a raw ArcGIS item ID or an Experience Builder URL.

    Supported examples:
    - abcdef1234567890abcdef1234567890
    - https://experience.arcgis.com/experience/abcdef1234567890abcdef1234567890
    - https://experience.arcgis.com/builder/?id=abcdef1234567890abcdef1234567890
    - https://www.arcgis.com/home/item.html?id=abcdef1234567890abcdef1234567890
    """
    value = value.strip()

    raw_id_match = re.fullmatch(r"[a-fA-F0-9]{32}", value)
    if raw_id_match:
        return value

    patterns = [
        r"/experience/([a-fA-F0-9]{32})",
        r"[?&]id=([a-fA-F0-9]{32})",
        r"/apps/experiencebuilder/experience/([a-fA-F0-9]{32})",
    ]

    for pattern in patterns:
        match = re.search(pattern, value)
        if match:
            return match.group(1)

    raise ValueError(
        "Could not extract a valid 32-character ArcGIS item ID from the value provided."
    )


def infer_portal_from_url(value: str, fallback_portal: str = DEFAULT_PORTAL) -> str:
    """
    Best-effort portal inference.

    For MVP, we default to ArcGIS Online unless the user supplies --portal.
    This function mainly helps when the pasted URL clearly belongs to a portal domain.
    """
    value = value.strip()
    if not value.lower().startswith(("http://", "https://")):
        return fallback_portal

    # Public Experience Builder AGOL apps often use experience.arcgis.com.
    # The actual portal may still be ArcGIS Online, so use fallback.
    if "experience.arcgis.com" in value.lower():
        return fallback_portal

    # Common ArcGIS Online item URL.
    if "arcgis.com/home/item.html" in value.lower():
        return "https://www.arcgis.com"

    return fallback_portal


def get_sharing_flags(item: Any) -> Tuple[bool, bool]:
    access = getattr(item, "access", "") or ""
    is_public = access.lower() == "public"
    is_org_shared = access.lower() in {"org", "public"}
    return is_public, is_org_shared


def get_group_titles(item: Any) -> str:
    try:
        groups = item.shared_with.get("groups", []) if item.shared_with else []
        titles = []
        for group in groups:
            if isinstance(group, dict):
                titles.append(group.get("title", ""))
            else:
                titles.append(getattr(group, "title", ""))
        return "; ".join(t for t in titles if t)
    except Exception:
        return ""


def get_group_count(item: Any) -> int:
    try:
        groups = item.shared_with.get("groups", []) if item.shared_with else []
        return len(groups)
    except Exception:
        return 0


def detect_possible_exb_config(item_data: Optional[Dict[str, Any]]) -> bool:
    if not isinstance(item_data, dict):
        return False

    likely_keys = {
        "widgets",
        "dataSources",
        "pages",
        "views",
        "layouts",
        "appConfig",
        "theme",
        "attributes",
    }

    return any(key in item_data for key in likely_keys)


def compact_top_level_keys(item_data: Optional[Dict[str, Any]]) -> str:
    if not isinstance(item_data, dict):
        return ""
    return "; ".join(sorted(str(k) for k in item_data.keys()))


def save_raw_json(app_item_id: str, title: str, item_data: Optional[Dict[str, Any]]) -> Path:
    safe_title = sanitize_filename(title)
    timestamp = dt.now().strftime("%Y%m%d_%H%M%S")
    output_path = RAW_JSON_DIR / f"{safe_title}_{app_item_id}_{timestamp}.json"

    payload = item_data if item_data is not None else {}
    with output_path.open("w", encoding="utf-8") as f:
        json.dump(payload, f, indent=2, ensure_ascii=False)

    return output_path


def write_app_summary_csv(metadata: AppMetadata) -> Path:
    timestamp = dt.now().strftime("%Y%m%d_%H%M%S")
    output_path = CSV_DIR / f"app_summary_{metadata.app_item_id}_{timestamp}.csv"

    with output_path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=list(asdict(metadata).keys()))
        writer.writeheader()
        writer.writerow(asdict(metadata))

    return output_path


# -----------------------------------------------------------------------------
# ArcGIS connection and scanning
# -----------------------------------------------------------------------------

def connect_to_portal(portal_url: str, username: Optional[str], anonymous: bool) -> Any:
    if GIS is None:
        raise ImportError(
            "The arcgis package is not installed. Install it with: pip install arcgis"
        )

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
        raise RuntimeError(
            f"Item not found or not accessible with the current credentials: {item_id}"
        )
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
        return None, f"Could not read item data/config JSON: {exc}"


def scan_app_metadata(portal_url: str, item: Any, item_data: Optional[Dict[str, Any]], raw_json_path: Path, data_issue: str) -> AppMetadata:
    is_public, is_org_shared = get_sharing_flags(item)
    possible_exb_config = detect_possible_exb_config(item_data)
    item_data_readable = isinstance(item_data, dict)

    issues = []
    item_type = getattr(item, "type", "") or ""

    if "Experience" not in item_type and "Web Experience" not in item_type:
        issues.append(f"Item type is '{item_type}', which may not be an Experience Builder app.")

    if data_issue:
        issues.append(data_issue)

    if item_data_readable and not possible_exb_config:
        issues.append("Item data was readable, but it does not look like a standard Experience Builder config.")

    if not getattr(item, "url", None):
        issues.append("Item URL is empty or unavailable.")

    return AppMetadata(
        scan_timestamp_utc=now_utc_string(),
        portal_url=portal_url,
        app_item_id=getattr(item, "id", "") or "",
        title=getattr(item, "title", "") or "",
        item_type=item_type,
        owner=getattr(item, "owner", "") or "",
        created_utc=utc_from_esri_millis(getattr(item, "created", None)),
        modified_utc=utc_from_esri_millis(getattr(item, "modified", None)),
        access=getattr(item, "access", "") or "",
        is_public=is_public,
        is_org_shared=is_org_shared,
        group_count=get_group_count(item),
        groups=get_group_titles(item),
        num_views=getattr(item, "numViews", None),
        url=getattr(item, "url", "") or "",
        homepage=getattr(item, "homepage", "") or "",
        size=getattr(item, "size", None),
        type_keywords=safe_join(getattr(item, "typeKeywords", [])),
        tags=safe_join(getattr(item, "tags", [])),
        description_present=bool(getattr(item, "description", None)),
        snippet_present=bool(getattr(item, "snippet", None)),
        license_info_present=bool(getattr(item, "licenseInfo", None)),
        item_data_readable=item_data_readable,
        item_data_top_level_keys=compact_top_level_keys(item_data),
        possible_exb_config=possible_exb_config,
        raw_json_path=str(raw_json_path),
        issue_summary=" | ".join(issues),
    )


# -----------------------------------------------------------------------------
# CLI
# -----------------------------------------------------------------------------

def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Scan an ArcGIS Experience Builder app item and export metadata/config JSON."
    )

    input_group = parser.add_mutually_exclusive_group(required=True)
    input_group.add_argument(
        "--app-url",
        help="Experience Builder app URL or ArcGIS item URL.",
    )
    input_group.add_argument(
        "--item-id",
        help="32-character ArcGIS item ID for the Experience Builder app.",
    )

    parser.add_argument(
        "--portal",
        default=None,
        help="Portal URL. Example: https://www.arcgis.com or https://yourportal.domain.com/portal",
    )
    parser.add_argument(
        "--username",
        default=None,
        help="Portal username. If omitted, the scan runs anonymously.",
    )
    parser.add_argument(
        "--anonymous",
        action="store_true",
        help="Force anonymous scan even if username is omitted.",
    )

    return parser.parse_args()


def main() -> int:
    log_path = setup_logging()
    args = parse_args()

    try:
        input_value = args.app_url or args.item_id
        app_item_id = extract_item_id(input_value)
        portal_url = args.portal or infer_portal_from_url(input_value, DEFAULT_PORTAL)

        logging.info("Starting LHI ExB App Inspector - Script 01")
        logging.info("Resolved portal URL: %s", portal_url)
        logging.info("Resolved app item ID: %s", app_item_id)

        gis = connect_to_portal(
            portal_url=portal_url,
            username=args.username,
            anonymous=args.anonymous,
        )

        item = fetch_item(gis, app_item_id)
        logging.info("Item found: %s | Type: %s | Owner: %s", item.title, item.type, item.owner)

        item_data, data_issue = read_item_data(item)
        raw_json_path = save_raw_json(app_item_id, getattr(item, "title", "untitled"), item_data)
        logging.info("Raw item data saved to: %s", raw_json_path)

        metadata = scan_app_metadata(
            portal_url=portal_url,
            item=item,
            item_data=item_data,
            raw_json_path=raw_json_path,
            data_issue=data_issue,
        )

        csv_path = write_app_summary_csv(metadata)
        logging.info("App summary CSV saved to: %s", csv_path)

        print("\n=== LHI ExB App Inspector: Script 01 Complete ===")
        print(f"App title: {metadata.title}")
        print(f"Item ID: {metadata.app_item_id}")
        print(f"Type: {metadata.item_type}")
        print(f"Owner: {metadata.owner}")
        print(f"Access: {metadata.access}")
        print(f"Item data readable: {metadata.item_data_readable}")
        print(f"Possible ExB config: {metadata.possible_exb_config}")
        print(f"CSV output: {csv_path}")
        print(f"Raw JSON output: {raw_json_path}")
        print(f"Log file: {log_path}")

        if metadata.issue_summary:
            print("\nWarnings:")
            print(metadata.issue_summary)

        return 0

    except Exception as exc:
        logging.exception("Scan failed: %s", exc)
        print("\nScan failed. Check the log file for details:")
        print(log_path)
        print(f"Error: {exc}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
