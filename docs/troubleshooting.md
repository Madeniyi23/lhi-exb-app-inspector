# Troubleshooting

## Streamlit Open master HTML button does not work

Use the `Open batch folder` button and open:

```text
master/exb_app_inspector_master_report.html
```

from File Explorer.

## Individual report links do not work from downloaded master HTML

Downloading only the master HTML does not include the related app folders.

Use one of these instead:

1. Open the master HTML from the local batch folder.
2. Download the full batch ZIP, extract it, then open the master HTML from inside the extracted folder.

## App failed in the master report

Expand the row in the master HTML and review:

- failed stage code
- failed stage name
- error message
- child stage logs

## Stage timeout behavior

Script 06 enforces hard stage-level timeouts. If one app hangs, it should be marked failed/timed out and Script 07 should continue to the next app.
