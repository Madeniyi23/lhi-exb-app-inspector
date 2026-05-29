# Security Notes

- Do not commit real scan outputs from production organizations.
- Do not store passwords in scripts or CSVs.
- The scripts use `getpass` for password entry.
- Real outputs can expose app names, item IDs, service URLs, internal hostnames, and layer names.
- Review `.gitignore` before pushing.
- Use redacted screenshots for public demos.
