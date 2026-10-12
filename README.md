# Job Catalog

**879 curated job-vacancy sources**: categories, subcategories, remote/regional boards, startup and gamedev lists, AI & Data boards — focused on Moldova/EU market.

## Structure
- `data.json` — the whole catalog, machine-readable. AI agents can fetch and search it directly:
  ```
  https://raw.githubusercontent.com/FindLyTen/job-catalog/main/data.json
  ```
- `app/` — minimal web UI + API that renders this catalog (self-hostable)

## Usage
Used by scrapers/parsers (job_parser project) as the source-of-truth list of boards to scan. Public so any AI agent can read it without tokens.
