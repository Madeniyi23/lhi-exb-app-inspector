# Troubleshooting

## App failed in the master report

Expand the row in the master HTML. Review:

- failed stage code
- failed stage name
- error message
- child stage logs

## Common failed stages

| Stage | Meaning |
|---|---|
| Stage 01 | App metadata/config issue |
| Stage 02 | ExB dependency extraction issue |
| Stage 03 | Web map scan issue |
| Stage 04 | Layer health check issue |
| Stage 08 | Layer identity resolver issue |
| Stage 05 | Sharing compatibility/report issue |

## Where to find logs

Each app folder includes:

```text
app_scan.log
logs/
```

The `logs/` folder contains child-stage logs created during the scan.

## Stage timeout behavior

Script 06 enforces hard stage-level timeouts. If one app hangs, it should be marked failed/timed out and Script 07 should continue to the next app.

Default stage timeouts:

```text
Stage 01: 180 seconds
Stage 02: 240 seconds
Stage 03: 300 seconds
Stage 04: 420 seconds
Stage 08: 420 seconds
Stage 05: 300 seconds
```

## Non-map apps

Some valid Experience Builder apps have no web maps, no data sources, or no layers. The pipeline now writes empty CSV files with headers and continues.
