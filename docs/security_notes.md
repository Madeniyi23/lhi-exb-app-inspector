# Security Notes

- Do not commit real scan outputs if they contain internal service URLs, app names, layer names, or organizational metadata.
- Do not store passwords in scripts, batch files, or CSV files.
- The runners use `getpass` and pass the password to child scripts using the temporary `LHI_ARCGIS_PASSWORD` environment variable for the active process only.
- Review `.gitignore` before pushing to GitHub.
- If publishing publicly, use synthetic screenshots or redacted reports.
