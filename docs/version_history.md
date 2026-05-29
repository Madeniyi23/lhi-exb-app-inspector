# Version History

## v1.1 stable MVP

- Integrated Script 08 into the full pipeline
- Added layer identity resolver outputs to packaged batches
- Added child-stage log packaging
- Refined internal-service classifier to avoid false positives from business terms like "Development"
- Fixed Script 04/05 imports introduced by classifier refinement
- Confirmed clean 11-app batch run with 11 successful scans

## v1.0-alpha

- First GitHub-ready MVP package
- Single-app and multi-app runners
- Batch packaging
- HTML/CSV outputs
- Internal service detection
- Failed-stage reporting

## Earlier milestones

- v0.8: improved multi-app master HTML report
- v0.8.1: better handling of missing/no REST URL health rows
- v0.8.2: internal host/service classification
- v0.8.3: failed-stage capture
- v0.9: batch folder packaging
