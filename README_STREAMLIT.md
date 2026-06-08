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
