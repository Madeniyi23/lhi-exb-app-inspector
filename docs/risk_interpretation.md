# Risk Interpretation

## Endpoint Health vs Action Severity

The report separates two concepts:

| Field | Meaning |
|---|---|
| Endpoint Health | Raw REST endpoint reachability/health result |
| Action Severity | Contextual severity based on app sharing, web map sharing, endpoint health, and active widget dependencies |

Example:

```text
Endpoint Health = critical
Action Severity = high
```

This can happen when the endpoint is not reachable from the scanner, but the app is org/internal rather than public-facing.

## Scanner context

The scanner's access context may differ from an authenticated internal user's context.

For internal/org apps, an unreachable internal service does not automatically prove the app is broken. It means the dependency should be validated against:

- intended user audience
- required VPN/network
- service availability
- authentication/sharing permissions

## Public app/web map mismatch

For public apps or public web maps, unreachable internal services are more severe because public users are unlikely to access internal endpoints.

## Not checked

`Endpoint Health = not checked` means there was no direct REST endpoint available for the health checker. This can happen with:

- group layers
- folder/container entries
- non-REST map entries
- layers without a direct service URL

It does not mean low risk.
