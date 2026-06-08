# Streamlit Local UI

Run:

```bat
python -m streamlit run app.py
```

If using embedded Python for the UI:

```bat
"C:\GIS\python-3.14.5-embed-amd64\python.exe" -m streamlit run app.py
```

In the sidebar, set the Python executable used by the backend scripts to ArcGIS Pro Python:

```text
C:\Program Files\ArcGIS\Pro\bin\Python\envs\arcgispro-py3\python.exe
```

The UI is a local wrapper over the existing script engine. It does not replace the backend scripts.


## v0.2.0 selective inspection workflow

After running discovery, use the discovery preview table to:

1. Filter apps by owner/access/status/search text.
2. Select apps manually or use all filtered rows.
3. Create a selected-apps CSV.
4. Go to Run Inspection.
5. Choose `Selected apps from UI`.
6. Run the scan.

This avoids unnecessary long full-batch scans when only a subset of apps needs review.
