# Version History

## v1.1.2 stability release

- Added REST-first Script 01 metadata/config retrieval with direct token generation.
- Added hard stage-level subprocess timeouts in Script 06.
- Added empty CSV handling for valid no-map/no-layer apps across Scripts 02, 03, 04, 05, and 08.
- Added Script 09 improvements for operational discovery:
  - `--mode broad`
  - `--status published-or-changed`
  - `--item-type web-experience-only`
  - `--exclude-templates`
  - `--limit`
- Confirmed 50-app operational test batch:
  - 50 apps scanned
  - 49 successful
  - 1 isolated Stage 03 failure

## v1.1 stable MVP

- Integrated Script 08 into the full pipeline.
- Added layer identity resolver outputs to packaged batches.
- Added child-stage log packaging.
- Refined internal-service classifier to avoid false positives from business terms like "Development".
- Fixed Script 04/05 imports introduced by classifier refinement.
- Confirmed clean 11-app batch run with 11 successful scans.

## v1.0-alpha

- First GitHub-ready MVP package.
- Single-app and multi-app runners.
- Batch packaging.
- HTML/CSV outputs.
- Internal service detection.
- Failed-stage reporting.
