@echo off
REM LHI ExB App Inspector - Streamlit Local UI launcher

cd /d "%~dp0"

echo Starting LHI ExB App Inspector Streamlit UI...
echo.

REM Recommended when using embedded Python for the UI:
REM "C:\GIS\python-3.14.5-embed-amd64\python.exe" -m streamlit run app.py

python -m streamlit run app.py

pause
