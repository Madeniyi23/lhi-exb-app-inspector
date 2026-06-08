# Version History

## v1.2.0 selective inspection workflow

- Added Streamlit UI v0.2.0.
- Added app selection after discovery.
- Added discovery preview filters by owner, access, status, and search text.
- Added manual checkbox selection workflow.
- Added "use all filtered rows" workflow.
- Added selected-apps CSV generation.
- Added Run Inspection input source options:
  - Selected apps from UI
  - Discovery-generated CSV
  - Manual/custom CSV
- Added queued-app preview before inspection.
- Confirmed targeted 3-app scan workflow.

## v1.1.3 report clarity release

- Added Streamlit UI v0.1.3 report open fix.
- Added `Open master HTML report` and `Open batch folder` support.
- Clarified that downloading master HTML only does not preserve individual report links.
- Refined Script 05 report severity model for internal/org apps.
- Renamed `Health Risk` to `Endpoint Health`.
- Renamed `Severity` to `Action Severity`.
- Replaced blank endpoint health with `not checked`.
- Added interpretation notes for scanner context, endpoint health, action severity, and operational risk.

## v1.1.2 stability release

- Added REST-first Script 01 metadata/config retrieval with direct token generation.
- Added hard stage-level subprocess timeouts in Script 06.
- Added empty CSV handling for valid no-map/no-layer apps across Scripts 02, 03, 04, 05, and 08.
- Added Script 09 improvements for operational discovery.
- Confirmed 50-app operational test batch.

## v1.1 stable MVP

- Integrated Script 08 into the full pipeline.
- Added layer identity resolver outputs to packaged batches.
- Added child-stage log packaging.
- Refined internal-service classifier to avoid false positives from business terms like "Development".

## v1.0-alpha

- First GitHub-ready MVP package.
- Single-app and multi-app runners.
- Batch packaging.
- HTML/CSV outputs.
- Internal service detection.
- Failed-stage reporting.
