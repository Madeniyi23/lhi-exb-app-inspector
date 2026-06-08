# Selective Inspection Workflow

## Purpose

The selective inspection workflow lets users choose which discovered Experience Builder apps should be scanned.

This avoids long full-batch scans when the user only wants to inspect a few apps.

## Workflow

```text
Discover apps
→ Filter discovery results
→ Select apps
→ Create selected-apps CSV
→ Run inspection from selected CSV
→ Review results
```

## Discovery filters

The Streamlit UI supports filtering discovered apps by:

- owner
- access level
- Experience Builder status
- search text

## Selection modes

The UI supports two modes:

| Mode | Use case |
|---|---|
| Manual checkbox selection | Pick specific apps one by one |
| Use all filtered rows | Filter first, then scan all matching apps |

## Output

The selected apps are written to:

```text
outputs/csv/selected_exb_apps_input_<timestamp>.csv
```

This CSV is compatible with Script 07.

## Recommended use

Start with a small selection, such as 3 to 5 apps, especially when testing a new organization or demoing the tool.
