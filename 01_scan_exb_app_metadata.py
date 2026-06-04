"""
LHI ExB App Inspector
Script 01: Scan Experience Builder App Metadata v1.1.2

Purpose:
- Connect to ArcGIS Online or ArcGIS Enterprise
- Fetch one Experience Builder app item by URL or Item ID
- Export app metadata to CSV
- Save the raw app item data/config JSON for later dependency inspection
- Create a scan log for troubleshooting

v1.1.2 update:
- Fully REST-first.
- Avoids ArcGIS API login/session/item hydration for Stage 01.
- Generates Portal token directly via /sharing/rest/generateToken when username/password are provided.
- Reads LHI_ARCGIS_PASSWORD from environment when launched by Script 06/07.
- Uses request timeouts for metadata and config retrieval.

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

import requests


DEFAULT_PORTAL = "https://www.arcgis.com"
SCRIPT_VERSION = "1.1.2"
OUTPUT_ROOT = Path("outputs")
RAW_JSON_DIR = OUTPUT_ROOT / "raw_json"
CSV_DIR = OUTPUT_ROOT / "csv"
LOG_DIR = OUTPUT_ROOT / "logs"


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
    if value in [None, ""]:
        return ""
    try:
        return dt.fromtimestamp(float(value) / 1000, tz=timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
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
    value = re.sub(r"[^A-Za-z0-9_\-]+", "_", (value or "").strip())
    return value[:120] if value else "untitled"


def extract_item_id(value: str) -> str:
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

    raise ValueError("Could not extract a valid 32-character ArcGIS item ID from the value provided.")


def infer_portal_from_url(value: str, fallback_portal: str = DEFAULT_PORTAL) -> str:
    value = value.strip()
    if not value.lower().startswith(("http://", "https://")):
        return fallback_portal
    if "experience.arcgis.com" in value.lower():
        return fallback_portal
    if "arcgis.com/home/item.html" in value.lower():
        return "https://www.arcgis.com"
    return fallback_portal


def sharing_rest_url(portal_url: str) -> str:
    portal = (portal_url or DEFAULT_PORTAL).rstrip("/")
    if portal.lower().endswith("/sharing/rest"):
        return portal
    return f"{portal}/sharing/rest"


def rest_json_get(url: str, params: Dict[str, Any], timeout: int) -> Dict[str, Any]:
    logging.info("REST GET: %s", url)
    response = requests.get(url, params=params, timeout=timeout)
    response.raise_for_status()

    payload = response.json()
    if isinstance(payload, dict) and "error" in payload:
        err = payload.get("error") or {}
        message = err.get("message", "Unknown REST error") if isinstance(err, dict) else str(err)
        details = err.get("details", []) if isinstance(err, dict) else []
        raise RuntimeError(f"Portal REST error: {message}; details={details}")

    return payload


def rest_json_post(url: str, data: Dict[str, Any], timeout: int) -> Dict[str, Any]:
    logging.info("REST POST: %s", url)
    response = requests.post(url, data=data, timeout=timeout)
    response.raise_for_status()

    payload = response.json()
    if isinstance(payload, dict) and "error" in payload:
        err = payload.get("error") or {}
        message = err.get("message", "Unknown REST error") if isinstance(err, dict) else str(err)
        details = err.get("details", []) if isinstance(err, dict) else []
        raise RuntimeError(f"Portal REST error: {message}; details={details}")

    return payload


def generate_token(portal_url: str, username: Optional[str], anonymous: bool, timeout: int) -> str:
    if anonymous or not username:
        logging.info("No username supplied or anonymous enabled. Running Stage 01 without token.")
        return ""

    password = os.getenv("LHI_ARCGIS_PASSWORD")
    if password:
        logging.info("Using password from LHI_ARCGIS_PASSWORD environment variable.")
    else:
        password = getpass.getpass(f"Password for {username}: ")

    url = f"{sharing_rest_url(portal_url)}/generateToken"
    payload = {
        "f": "json",
        "username": username,
        "password": password,
        "referer": "https://www.arcgis.com",
        "expiration": 60,
    }

    logging.info("Generating portal token for %s", username)
    result = rest_json_post(url, payload, timeout=timeout)
    token = result.get("token", "")

    if not token:
        raise RuntimeError("Token generation succeeded but no token was returned.")

    logging.info("Portal token generated successfully.")
    return str(token)


def fetch_item_metadata_rest(portal_url: str, item_id: str, token: str, timeout: int) -> Dict[str, Any]:
    url = f"{sharing_rest_url(portal_url)}/content/items/{item_id}"
    params: Dict[str, Any] = {"f": "json"}
    if token:
        params["token"] = token

    logging.info("Fetching item metadata via REST: %s", item_id)
    item = rest_json_get(url, params=params, timeout=timeout)

    if not item or not isinstance(item, dict):
        raise RuntimeError(f"Item metadata was empty or invalid for item ID: {item_id}")

    if not item.get("id"):
        raise RuntimeError(f"Item not found or not accessible with current credentials: {item_id}")

    return item


def read_item_data_rest(portal_url: str, item_id: str, token: str, timeout: int) -> Tuple[Optional[Dict[str, Any]], str]:
    url = f"{sharing_rest_url(portal_url)}/content/items/{item_id}/data"
    params: Dict[str, Any] = {"f": "json"}
    if token:
        params["token"] = token

    try:
        logging.info("Fetching item data/config via REST: %s", item_id)
        data = rest_json_get(url, params=params, timeout=timeout)

        if data is None:
            return None, "Item data returned None."
        if not isinstance(data, dict):
            return None, f"Item data is not a JSON object. Returned type: {type(data).__name__}"
        return data, ""

    except Exception as exc:
        return None, f"Could not read item data/config JSON via REST: {exc}"


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


def get_sharing_flags(item_json: Dict[str, Any]) -> Tuple[bool, bool]:
    access = str(item_json.get("access", "") or "").lower()
    is_public = access == "public"
    is_org_shared = access in {"org", "public"}
    return is_public, is_org_shared


def get_groups(item_json: Dict[str, Any]) -> Tuple[int, str]:
    groups = item_json.get("groups") or item_json.get("sharedWithGroups") or []
    if not isinstance(groups, list):
        return 0, ""

    titles = []
    for group in groups:
        if isinstance(group, dict):
            titles.append(str(group.get("title", "") or group.get("id", "")))
        else:
            titles.append(str(group))

    titles = [t for t in titles if t]
    return len(titles), "; ".join(titles)


def scan_app_metadata(
    portal_url: str,
    item_json: Dict[str, Any],
    item_data: Optional[Dict[str, Any]],
    raw_json_path: Path,
    data_issue: str,
) -> AppMetadata:
    is_public, is_org_shared = get_sharing_flags(item_json)
    group_count, groups = get_groups(item_json)
    possible_exb_config = detect_possible_exb_config(item_data)
    item_data_readable = isinstance(item_data, dict)
    item_type = item_json.get("type", "") or ""

    issues = []

    if "Experience" not in item_type and "Web Experience" not in item_type:
        issues.append(f"Item type is '{item_type}', which may not be an Experience Builder app.")

    if data_issue:
        issues.append(data_issue)

    if item_data_readable and not possible_exb_config:
        issues.append("Item data was readable, but it does not look like a standard Experience Builder config.")

    if not item_json.get("url"):
        issues.append("Item URL is empty or unavailable.")

    return AppMetadata(
        scan_timestamp_utc=now_utc_string(),
        portal_url=portal_url,
        app_item_id=item_json.get("id", "") or "",
        title=item_json.get("title", "") or "",
        item_type=item_type,
        owner=item_json.get("owner", "") or "",
        created_utc=utc_from_esri_millis(item_json.get("created")),
        modified_utc=utc_from_esri_millis(item_json.get("modified")),
        access=item_json.get("access", "") or "",
        is_public=is_public,
        is_org_shared=is_org_shared,
        group_count=group_count,
        groups=groups,
        num_views=item_json.get("numViews"),
        url=item_json.get("url", "") or "",
        homepage=item_json.get("homepage", "") or "",
        size=item_json.get("size"),
        type_keywords=safe_join(item_json.get("typeKeywords", [])),
        tags=safe_join(item_json.get("tags", [])),
        description_present=bool(item_json.get("description")),
        snippet_present=bool(item_json.get("snippet")),
        license_info_present=bool(item_json.get("licenseInfo")),
        item_data_readable=item_data_readable,
        item_data_top_level_keys=compact_top_level_keys(item_data),
        possible_exb_config=possible_exb_config,
        raw_json_path=str(raw_json_path),
        issue_summary=" | ".join(issues),
    )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Scan an ArcGIS Experience Builder app item and export metadata/config JSON."
    )

    input_group = parser.add_mutually_exclusive_group(required=True)
    input_group.add_argument("--app-url", help="Experience Builder app URL or ArcGIS item URL.")
    input_group.add_argument("--item-id", help="32-character ArcGIS item ID for the Experience Builder app.")

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
    parser.add_argument(
        "--rest-timeout",
        type=int,
        default=60,
        help="Timeout in seconds for Script 01 Portal REST token/metadata/data requests. Default: 60.",
    )

    return parser.parse_args()


def main() -> int:
    log_path = setup_logging()
    args = parse_args()

    try:
        input_value = args.app_url or args.item_id
        app_item_id = extract_item_id(input_value)
        portal_url = args.portal or infer_portal_from_url(input_value, DEFAULT_PORTAL)

        logging.info("Starting LHI ExB App Inspector - Script 01 v%s", SCRIPT_VERSION)
        logging.info("Resolved portal URL: %s", portal_url)
        logging.info("Resolved app item ID: %s", app_item_id)

        token = generate_token(
            portal_url=portal_url,
            username=args.username,
            anonymous=args.anonymous,
            timeout=args.rest_timeout,
        )

        item_json = fetch_item_metadata_rest(
            portal_url=portal_url,
            item_id=app_item_id,
            token=token,
            timeout=args.rest_timeout,
        )
        logging.info(
            "Item found via REST: %s | Type: %s | Owner: %s",
            item_json.get("title", ""),
            item_json.get("type", ""),
            item_json.get("owner", ""),
        )

        item_data, data_issue = read_item_data_rest(
            portal_url=portal_url,
            item_id=app_item_id,
            token=token,
            timeout=args.rest_timeout,
        )

        raw_json_path = save_raw_json(app_item_id, item_json.get("title", "untitled"), item_data)
        logging.info("Raw item data saved to: %s", raw_json_path)

        metadata = scan_app_metadata(
            portal_url=portal_url,
            item_json=item_json,
            item_data=item_data,
            raw_json_path=raw_json_path,
            data_issue=data_issue,
        )

        csv_path = write_app_summary_csv(metadata)
        logging.info("App summary CSV saved to: %s", csv_path)

        print(f"\n=== LHI ExB App Inspector: Script 01 v{SCRIPT_VERSION} Complete ===")
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
