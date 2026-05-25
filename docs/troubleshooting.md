# Troubleshooting

## Script 07 says an app failed

Open the master report and expand the failed app row. It should show:

- failed stage code
- failed stage name
- error message
- child log path

Common failed stages:

| Stage | Meaning |
|---|---|
| Stage 01 | App item metadata/config could not be read |
| Stage 02 | ExB dependency extraction failed |
| Stage 03 | Web map/layer scan failed |
| Stage 04 | Layer health check failed |
| Stage 05 | Sharing compatibility report failed |

## Invalid token behavior

Some public or non-federated ArcGIS Server services reject AGOL tokens. Script 04 uses anonymous-first testing by default to avoid false failures.

## Internal service detected

Internal host detection looks for patterns such as:

- `internal`
- `appint`
- `gisappint`
- `General_Int`
- `dev`
- `test`
- `uat`
- `staging`

This is a governance signal. It means the app may depend on an internal or environment-specific service.

## No REST URL

Some web map layers may not expose a normal REST URL. If no active widget depends on the layer, the report treats this as informational.
