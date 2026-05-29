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

## Internal service flags

Internal service detection is a governance signal. It does not automatically mean a service is broken. Review intended audience, VPN/network requirements, and sharing.
