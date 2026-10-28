# CLAUDE.md — repository guide

## What this repo is
A public catalog of 879 job-search sources in 18 categories, for humans and for
AI agents. There is no application code to run except an optional local Flask app.

## Source of truth
- `data.json` — the catalog. Keys: `cats` (id, name, color, subs) and `items`
  (id, name, url, tags, cat, sub). Stable field names; do not rename.
- Raw URL used by consumers:
  `https://raw.githubusercontent.com/FindLyTen/job-catalog/main/data.json`
- `clients_parser` (external) reads `items[].id, tags, sub, name, url`.
- Do not change `data.json` unless the task explicitly asks to edit the catalog.

## Generated files — never edit by hand
| File | What |
|---|---|
| `catalog.html` | offline page: search, filters, collapsible categories |
| `INDEX.md` | readable index by category/subcategory, anchors `cat-<id>` |
| `mindmap.md`, `mindmap.html` | Mermaid mind map (GitHub / offline) |
| `index.json` | counts + per-subcategory lists, schema `job-catalog-index-v1` |
| `index.jsonl` | one source per line, enriched |
| `schema.json` | plain description of fields |

Rebuild everything with:

```
python3 tools/build_views.py
```

The script reads `data.json`, regenerates the files above and validates:
JSON parses, all source URLs are present, no personal data patterns.

## View-only enrichment
Generated files add display fields; `data.json` stays untouched:
- country added to display names where a source belongs to one country;
- 37 sources without a subcategory got one (marked `?` in the page, `(view)` in the index);
- extra subcategories for Product & PM and Support & CS;
- "General Job Boards" split by region in `catalog.html` only.

## Rules for changes
- Keep `data.json` byte-identical unless the task is to edit the catalog itself.
- Never commit secrets, tokens, private keys, `.env` files or personal data —
  the repo is public.
- Do not commit backups (`.bak`), archives, staging folders or `_work/`.
- If you add a source: edit `data.json`, rerun `tools/build_views.py`, commit
  `data.json` plus regenerated files together.
- One commit per logical change; do not force-push; do not rewrite history.
