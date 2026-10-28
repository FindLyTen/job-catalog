#!/usr/bin/env python3
"""Build display views and machine indexes from data.json.

Reads data.json (never modifies it) and writes, next to it:
  catalog.html   offline human page (search, filters, collapsible categories)
  INDEX.md       readable Markdown index by category / subcategory
  mindmap.html   offline Mermaid mind map of all categories
  mindmap.md     Mermaid source of the same map
  index.json     machine index, schema job-catalog-index-v1
  index.jsonl    one source per line
  schema.json    plain description of the machine format

View-only additions (country in display names, filled subcategories, doubtful
marks) never touch data.json. Run: python tools/build_views.py
"""
from __future__ import annotations

import hashlib
import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA_PATH = ROOT / "data.json"
SCHEMA_ID = "job-catalog-index-v1"

STATUS_TAGS = {"remote", "login", "freelance", "eu"}
COUNTRY_ALIAS = {
    "ua": "Ukraine",
    "md": "Moldova",
    "nz": "New Zealand",
    "usa": "United States",
    "uk": "United Kingdom",
    "uae": "United Arab Emirates",
}
EUROPE = {
    "Austria", "Belgium", "Bosnia", "Bulgaria", "Croatia", "Cyprus", "Czechia",
    "Denmark", "Estonia", "Finland", "France", "Georgia", "Germany", "Greece",
    "Hungary", "Iceland", "Ireland", "Italy", "Latvia", "Lithuania", "Moldova",
    "Netherlands", "Norway", "Poland", "Portugal", "Romania", "Serbia",
    "Slovakia", "Slovenia", "Spain", "Sweden", "Switzerland", "United Kingdom",
}
NORTH_AMERICA = {"United States", "Canada"}
LATIN_AMERICA = {"Brazil", "Chile"}
ASIA_PACIFIC = {"Australia", "China", "Japan", "New Zealand", "Singapore", "Vietnam"}
MIDDLE_EAST = {"United Arab Emirates"}
REGIONS = ["Europe", "North America", "Latin America", "Asia-Pacific",
           "Middle East", "Remote / Global", "No region tag"]

# View-only subcategory per item id. Only items whose subcategory is missing in
# data.json (37 items), plus the two categories that have no subs at all.
VIEW_SUB_BY_ID = {
    "u1783854195660": "writing",
    "u1784031507348": "data",
    "u1784475315341": "cloud",
    "u1789743837634": "customer-success",
    "u1789743837646": "writing",
    "u1789743837651": "sd",
    "u1789743837658": "legal",
    "u1789743837783": "digital",
    "u1789743837787": "product",
    "u1789743837791": "teaching",
    "u1789743837826": "accounting",
    "u1789743837833": "community",
    "u1789743837908": "accounting",
    "u1789743837915": "nursing",
    "u1789743837952": "cloud",
    "u1789743837973": "product",
    "u1789743837979": "nursing",
    "u1789743837988": "ml",
    "u1789743837991": "customer-support",
    "u1789743838112": "accounting",
    "u1789743838173": "internships",
    "u1789743838189": "accounting",
    "u1789743838210": "cloud",
    "u1789743838217": "stack",
    "u1789743838298": "internships",
    "u1789743838299": "leadership",
    "u1789743838310": "teaching",
    "u1789743838382": "accounting",
    "u1789743837782": "internships",
    "u1789743838246": "technical-support",
    "u1789743838345": "cloud",
    "u1789743838350": "accounting",
    "u1789743838352": "customer-success",
    "u1789743838356": "sd",
    "u1789743838358": "sd",
    "u1789743838366": "stack",
    "u1789743838374": "digital",
}

# Categories that have no subs in data.json get view-only groups.
EXTRA_SUBS = {
    "prodmg": [
        ["product", "Product Management"],
        ["leadership", "PM Leadership & CTO"],
        ["community", "Product Community"],
    ],
    "support": [
        ["customer-support", "Customer Support"],
        ["customer-success", "Customer Success"],
        ["technical-support", "Technical Support"],
    ],
}

FORBIDDEN = [
    re.compile(r"\bM[a]xim\b", re.I),
    re.compile(r"max[s]mirnov", re.I),
    re.compile(r"\bS[m]irnov\b", re.I),
    re.compile(r"Ch[i]sinau", re.I),
    re.compile(r"gm[a]il\.com", re.I),
    re.compile(r"gh[pousr]_[A-Za-z0-9]{20,}"),
    re.compile(r"github_pat_[A-Za-z0-9_]{20,}"),
    re.compile(r"BEGIN [A-Z ]*PRIVATE KEY"),
]


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def esc_html(s: str) -> str:
    return (str(s).replace("&", "&amp;").replace("<", "&lt;")
            .replace(">", "&gt;").replace('"', "&quot;"))


def md_text(s: str) -> str:
    return str(s).replace("\\", "\\\\").replace("[", "\\[").replace("]", "\\]")


def mermaid_text(s: str) -> str:
    s = str(s)
    for ch in "()[]{}<>|":
        s = s.replace(ch, "")
    s = s.replace("&", "and").replace(":", " -").replace("#", "")
    return re.sub(r"\s+", " ", s).strip()


def normalize_url(u: str) -> str:
    return str(u or "").strip().rstrip("/").lower()


def country_of(tag: str) -> str | None:
    key = tag.strip().lower()
    if key in STATUS_TAGS:
        return None
    return COUNTRY_ALIAS.get(key, tag.strip())


def regions_of(countries: list[str], remote: bool, has_eu: bool) -> list[str]:
    out = []
    if any(c in EUROPE for c in countries):
        out.append("Europe")
    if any(c in NORTH_AMERICA for c in countries):
        out.append("North America")
    if any(c in LATIN_AMERICA for c in countries):
        out.append("Latin America")
    if any(c in ASIA_PACIFIC for c in countries):
        out.append("Asia-Pacific")
    if any(c in MIDDLE_EAST for c in countries):
        out.append("Middle East")
    if remote or has_eu or not countries:
        out.append("Remote / Global")
    if not out:
        out.append("No region tag")
    if not countries and not remote and not has_eu:
        out = ["No region tag"]
    return sorted(set(out), key=REGIONS.index)


def build_cats(data: dict) -> tuple[list[dict], dict[str, list[list[str]]]]:
    cats = []
    for c in data["cats"]:
        subs = [[sid, sname] for sid, sname in (c.get("subs") or [])]
        subs += EXTRA_SUBS.get(c["id"], [])
        cats.append({"id": c["id"], "name": c["name"],
                     "color": c.get("color", "#64748b"), "subs": subs})
    return cats, {c["id"]: c["subs"] for c in cats}


def enrich(data: dict, cats: list[dict]) -> list[dict]:
    sub_names = {c["id"]: {sid: sname for sid, sname in c["subs"]} for c in cats}
    cat_names = {c["id"]: c["name"] for c in cats}
    items = []
    for it in data["items"]:
        tags = [str(t) for t in (it.get("tags") or [])]
        countries = []
        for t in tags:
            cn = country_of(t)
            if cn and cn not in countries:
                countries.append(cn)
        tl = {t.lower() for t in tags}
        remote = "remote" in tl
        has_eu = "eu" in tl
        regions = regions_of(countries, remote, has_eu)

        cid = it.get("cat", "")
        src_sub = it.get("sub") or ""
        view_sub = src_sub or VIEW_SUB_BY_ID.get(it.get("id"), "")
        doubtful = not src_sub
        sub_name = sub_names.get(cid, {}).get(view_sub, view_sub.replace("-", " ").title() if view_sub else "Other")

        display = it["name"]
        if countries:
            if len(countries) == 1:
                display = f"{it['name']} ({countries[0]})"
            else:
                display = f"{it['name']} ({countries[0]} +{len(countries) - 1})"

        items.append({
            "id": it["id"],
            "name": it["name"],
            "display_name": display,
            "url": it["url"],
            "category": cid,
            "category_name": cat_names.get(cid, cid),
            "subcategory": view_sub,
            "subcategory_name": sub_name,
            "subcategory_source": "data" if src_sub else "view",
            "doubtful_subcategory": doubtful,
            "tags": tags,
            "countries": countries,
            "regions": regions,
            "remote": remote,
            "login": "login" in tl,
            "freelance": "freelance" in tl,
        })
    items.sort(key=lambda x: (x["category_name"].lower(), x["subcategory_name"].lower(),
                              x["display_name"].lower(), x["url"]))
    return items


# ---------------------------------------------------------------- HTML page

HTML_TEMPLATE = r"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Job Catalog — __COUNT__ sources</title>
<style>
:root{
  --bg:#f7f8fa; --panel:#ffffff; --ink:#17202a; --muted:#667085;
  --line:#e4e7ec; --accent:#2563eb; --accent-soft:#eff4ff; --chip:#f2f4f7;
  --radius:14px; --shadow:0 1px 2px rgba(16,24,40,.05),0 8px 24px rgba(16,24,40,.05);
}
*{box-sizing:border-box}
html,body{margin:0;padding:0}
body{background:var(--bg);color:var(--ink);font:15px/1.5 -apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,Arial,sans-serif}
a{color:var(--accent);text-decoration:none}
a:hover{text-decoration:underline}
.wrap{max-width:1180px;margin:0 auto;padding:0 20px}
header.top{background:linear-gradient(180deg,#ffffff 0%,#fbfcfe 100%);border-bottom:1px solid var(--line);padding:26px 0 18px;margin-bottom:22px}
h1{font-size:26px;line-height:1.2;margin:0 0 4px;letter-spacing:-.02em}
.sub{margin:0;color:var(--muted);font-size:14px}
.controls{display:grid;grid-template-columns:minmax(220px,1.6fr) repeat(4,minmax(130px,.75fr)) auto;gap:10px;margin-top:18px;align-items:center}
.controls input[type=search],.controls select{width:100%;height:40px;border:1px solid var(--line);border-radius:10px;background:#fff;padding:0 12px;font:inherit;color:var(--ink);outline:none}
.controls input[type=search]:focus,.controls select:focus{border-color:var(--accent);box-shadow:0 0 0 3px rgba(37,99,235,.12)}
.chk{display:flex;align-items:center;gap:7px;height:40px;padding:0 12px;border:1px solid var(--line);border-radius:10px;background:#fff;white-space:nowrap;cursor:pointer;user-select:none}
.chk input{accent-color:var(--accent);width:16px;height:16px}
.btn{height:40px;border:1px solid var(--line);background:#fff;border-radius:10px;padding:0 14px;font:inherit;color:var(--ink);cursor:pointer;white-space:nowrap}
.btn:hover{border-color:#c9ced6;background:#fafbfc}
.statusbar{display:flex;flex-wrap:wrap;gap:14px;align-items:center;margin-top:12px;color:var(--muted);font-size:13.5px}
.statusbar b{color:var(--ink)}
.statusbar .spacer{flex:1}
.statusbar .btn{height:32px;padding:0 10px;font-size:13px}
main{padding-bottom:40px}
details.cat{background:var(--panel);border:1px solid var(--line);border-radius:var(--radius);box-shadow:var(--shadow);margin:0 0 12px;overflow:hidden}
details.cat>summary{list-style:none;cursor:pointer;display:flex;align-items:center;gap:11px;padding:15px 18px;font-weight:650;font-size:16.5px;letter-spacing:-.01em}
details.cat>summary::-webkit-details-marker{display:none}
details.cat>summary .chev{margin-left:auto;color:#98a2b3;transition:transform .15s ease;font-size:13px}
details.cat[open]>summary .chev{transform:rotate(90deg)}
.dot{width:10px;height:10px;border-radius:50%;flex:none;box-shadow:0 0 0 4px rgba(0,0,0,.03)}
.cnt{font-weight:600;color:var(--muted);font-size:13px;background:var(--chip);border-radius:999px;padding:2px 9px;line-height:1.6}
.catbody{padding:2px 18px 16px;border-top:1px solid var(--line)}
.subgroup{margin-top:14px}
.subgroup h3{font-size:13px;text-transform:uppercase;letter-spacing:.06em;color:var(--muted);margin:0 0 7px;display:flex;align-items:center;gap:8px}
.subgroup ul{list-style:none;margin:0;padding:0;display:grid;grid-template-columns:repeat(auto-fill,minmax(330px,1fr));gap:2px 14px}
.item{display:flex;align-items:baseline;gap:7px;padding:7px 9px;border-radius:9px;min-width:0}
.item:hover{background:#f8fafc}
.item a{font-weight:550;overflow-wrap:anywhere}
.item .co{color:var(--muted);font-weight:400;font-size:13px;white-space:nowrap}
.chips{display:inline-flex;gap:4px;flex-wrap:wrap;margin-left:auto}
.chip{font-size:11px;line-height:1.6;color:#475467;background:var(--chip);border-radius:6px;padding:0 6px;white-space:nowrap}
.chip.remote{color:#067647;background:#ecfdf3}
.chip.login{color:#b54708;background:#fffaeb}
.chip.free{color:#6941c6;background:#f4f3ff}
.mark{font-size:11px;color:#b54708;background:#fffaeb;border:1px solid #fedf89;border-radius:6px;padding:0 5px;line-height:1.6;cursor:help}
.empty{display:none;text-align:center;color:var(--muted);padding:60px 20px;background:#fff;border:1px dashed var(--line);border-radius:var(--radius)}
.empty.show{display:block}
footer{border-top:1px solid var(--line);color:var(--muted);font-size:12.5px;padding:18px 0 34px;margin-top:30px}
footer code{background:var(--chip);border-radius:5px;padding:1px 5px}
@media (max-width:980px){.controls{grid-template-columns:1fr 1fr}.controls input[type=search]{grid-column:1/-1}.chk{justify-content:center}}
@media (max-width:620px){.controls{grid-template-columns:1fr}.subgroup ul{grid-template-columns:1fr}.wrap{padding:0 12px}header.top{padding-top:20px}}
</style>
</head>
<body>
<header class="top">
  <div class="wrap">
    <h1>Job Catalog</h1>
    <p class="sub"><b>__COUNT__</b> sources in <b>__CATCOUNT__</b> categories. Data file: <code>data.json</code> (not changed). Some labels are display-only. · <a href="mindmap.html">Mind map</a> · <a href="INDEX.md">Text index</a></p>
    <div class="controls">
      <input id="q" type="search" placeholder="Search name, site, tag, country…" autocomplete="off" aria-label="Search">
      <select id="fcat" aria-label="Category"><option value="">All categories</option></select>
      <select id="fsub" aria-label="Subcategory" disabled><option value="">All subcategories</option></select>
      <select id="fco" aria-label="Country"><option value="">All countries</option></select>
      <select id="frg" aria-label="Region"><option value="">All regions</option></select>
      <label class="chk"><input id="frem" type="checkbox"> Remote only</label>
    </div>
    <div class="statusbar">
      <span><b id="shown">__COUNT__</b> of __COUNT__ shown</span>
      <span class="spacer"></span>
      <button class="btn" id="expand" type="button">Expand all</button>
      <button class="btn" id="collapse" type="button">Collapse all</button>
      <button class="btn" id="reset" type="button">Reset filters</button>
    </div>
  </div>
</header>
<main class="wrap">
  <div id="list"></div>
  <div class="empty" id="empty">Nothing matches these filters.</div>
</main>
<footer class="wrap">
  Generated __GENERATED__ from <code>data.json</code> sha256 <code>__HASH_SHORT__</code>.
  Country in names, filled subcategories and the “?” mark are display-only; <code>data.json</code> itself is unchanged.
  The “?” mark means the subcategory was added in this view, not in the data file.
</footer>
<script>
const CATS = /*__CATS__*/;
const ITEMS = /*__ITEMS__*/;
const META = /*__META__*/;
const list = document.getElementById('list');
const q = document.getElementById('q');
const fcat = document.getElementById('fcat');
const fsub = document.getElementById('fsub');
const fco = document.getElementById('fco');
const frg = document.getElementById('frg');
const frem = document.getElementById('frem');
const shownEl = document.getElementById('shown');
const emptyEl = document.getElementById('empty');

function el(tag, cls, text){ const n=document.createElement(tag); if(cls) n.className=cls; if(text!=null) n.textContent=text; return n; }
function safeUrl(u){ return /^https?:\/\//i.test(u) ? u : null; }

const byCat = new Map();
for (const it of ITEMS){ if(!byCat.has(it.category)) byCat.set(it.category, []); byCat.get(it.category).push(it); }

const subCounts = new Map();
for (const c of CATS){ subCounts.set(c.id, new Map()); }
for (const it of ITEMS){ subCounts.get(it.category).set(it.subcategory, (subCounts.get(it.category).get(it.subcategory)||0)+1); }

const nodes = [];
for (const c of CATS){
  const det = el('details','cat');
  det.dataset.cat = c.id;
  const sum = el('summary');
  const dot = el('span','dot'); dot.style.background = c.color || '#64748b';
  const name = el('span','cname', c.name);
  const cnt = el('span','cnt', String(byCat.get(c.id).length));
  cnt.dataset.total = String(byCat.get(c.id).length);
  const chev = el('span','chev','▶');
  sum.append(dot, name, cnt, chev);
  det.append(sum);

  const body = el('div','catbody');
  const subs = c.subs.length ? c.subs : [...subCounts.get(c.id).keys()].map(s => [s, s]);
  for (const [sid, sname] of subs){
    const items = (byCat.get(c.id)||[]).filter(x => x.subcategory === sid);
    if (!items.length) continue;
    const group = el('section','subgroup');
    group.dataset.sub = sid;
    const h = el('h3');
    h.append(el('span', null, sname));
    const sc = el('span','cnt', String(items.length));
    sc.dataset.total = String(items.length);
    h.append(sc);
    group.append(h);
    const ul = el('ul');
    for (const it of items){
      const li = el('li','item');
      li.dataset.cat = it.category; li.dataset.sub = it.subcategory;
      li.dataset.co = it.countries.join('|'); li.dataset.rg = it.regions.join('|');
      li.dataset.rem = it.remote ? '1' : '0'; li.dataset.doubt = it.doubtful_subcategory ? '1' : '0';
      li.dataset.search = [it.name, it.display_name, it.url, it.category_name,
        it.subcategory_name, it.tags.join(' '), it.countries.join(' '), it.regions.join(' ')].join(' ').toLowerCase();

      const href = safeUrl(it.url);
      const a = el(href ? 'a' : 'span', null, it.name + (it.countries.length === 1 ? ' (' + it.countries[0] + ')' : ''));
      if (href){ a.href = href; a.target = '_blank'; a.rel = 'noopener noreferrer'; }
      if (it.countries.length > 1){
        const more = el('span','co', ' +' + (it.countries.length-1));
        a.append(more);
      }
      li.append(a);
      if (it.countries.length > 1) li.title = it.countries.join(', ');
      if (it.doubtful_subcategory){
        const m = el('span','mark','?');
        m.title = 'Subcategory added in this view (not in data.json)';
        li.append(m);
      }
      const chips = el('span','chips');
      if (it.remote) chips.append(el('span','chip remote','Remote'));
      const rest = it.tags.filter(t => !it.countries.includes(countryOfTag(t)) && !['remote','eu'].includes(t.toLowerCase()));
      for (const t of rest){
        const cls = t.toLowerCase()==='login' ? 'chip login' : (t.toLowerCase()==='freelance' ? 'chip free' : 'chip');
        chips.append(el('span', cls, t));
      }
      if (chips.childNodes.length) li.append(chips);
      ul.append(li);
    }
    group.append(ul);
    body.append(group);
  }
  det.append(body);
  list.append(det);
  nodes.push(det);
}

function countryOfTag(t){
  const map = {'ua':'Ukraine','md':'Moldova','nz':'New Zealand','usa':'United States','uk':'United Kingdom','uae':'United Arab Emirates'};
  return map[t.toLowerCase()] || t;
}

for (const c of CATS){ const o=el('option',null,c.name); o.value=c.id; fcat.append(o); }
for (const c of META.countries){ const o=el('option',null,c.name + ' (' + c.count + ')'); o.value=c.name; fco.append(o); }
for (const r of META.regions){ const o=el('option',null,r); o.value=r; frg.append(o); }

function rebuildSubs(){
  const cid = fcat.value;
  fsub.innerHTML = '';
  fsub.append(new Option(cid ? 'All subcategories' : 'Choose a category first',''));
  if (!cid){ fsub.disabled = true; return; }
  fsub.disabled = false;
  const cat = CATS.find(c => c.id === cid);
  for (const [sid,sname] of cat.subs){
    const n = subCounts.get(cid).get(sid)||0;
    if (n) fsub.append(new Option(sname + ' (' + n + ')', sid));
  }
}

function filtersActive(){
  return !!(q.value.trim() || fcat.value || fsub.value || fco.value || frg.value || frem.checked);
}

function applyFilters(autoOpen){
  const term = q.value.trim().toLowerCase();
  const cid = fcat.value, sid = fsub.value, co = fco.value, rg = frg.value, rem = frem.checked;
  let total = 0; let anyCat = false;
  for (const det of nodes){
    let catShown = 0;
    for (const group of det.querySelectorAll('.subgroup')){
      let subShown = 0;
      for (const li of group.querySelectorAll('.item')){
        let ok = true;
        if (cid && li.dataset.cat !== cid) ok = false;
        if (ok && sid && li.dataset.sub !== sid) ok = false;
        if (ok && co && !('|'+li.dataset.co+'|').includes('|'+co+'|')) ok = false;
        if (ok && rg && !('|'+li.dataset.rg+'|').includes('|'+rg+'|')) ok = false;
        if (ok && rem && li.dataset.rem !== '1') ok = false;
        if (ok && term && !li.dataset.search.includes(term)) ok = false;
        li.hidden = !ok;
        if (ok) subShown++;
      }
      group.hidden = subShown === 0;
      const sc = group.querySelector('h3 .cnt');
      if (sc) sc.textContent = filtersActive() ? (subShown + ' / ' + sc.dataset.total) : sc.dataset.total;
      catShown += subShown;
    }
    det.hidden = catShown === 0;
    const cc = det.querySelector('summary .cnt');
    if (cc) cc.textContent = filtersActive() ? (catShown + ' / ' + cc.dataset.total) : cc.dataset.total;
    total += catShown;
    if (catShown) anyCat = true;
    if (autoOpen) det.open = catShown > 0 && filtersActive();
  }
  shownEl.textContent = String(total);
  emptyEl.classList.toggle('show', total === 0);
  return anyCat;
}

fcat.addEventListener('change', () => { rebuildSubs(); applyFilters(true); });
fsub.addEventListener('change', () => applyFilters(true));
fco.addEventListener('change', () => applyFilters(true));
frg.addEventListener('change', () => applyFilters(true));
frem.addEventListener('change', () => applyFilters(true));
let timer = null;
q.addEventListener('input', () => { clearTimeout(timer); timer = setTimeout(() => applyFilters(true), 120); });
document.getElementById('reset').addEventListener('click', () => {
  q.value=''; fcat.value=''; fsub.value=''; fco.value=''; frg.value=''; frem.checked=false;
  rebuildSubs(); applyFilters(false); for (const d of nodes) d.open = false;
});
document.getElementById('expand').addEventListener('click', () => { for (const d of nodes) if(!d.hidden) d.open = true; });
document.getElementById('collapse').addEventListener('click', () => { for (const d of nodes) d.open = false; });
document.addEventListener('keydown', e => { if (e.key === '/' && document.activeElement !== q){ e.preventDefault(); q.focus(); } });
rebuildSubs();
applyFilters(false);
</script>
</body>
</html>
"""


def write_page(cats, items, meta, out: Path) -> None:
    html = (HTML_TEMPLATE
            .replace("/*__CATS__*/", json.dumps(cats, ensure_ascii=False, separators=(",", ":")).replace("</", "<\\/"))
            .replace("/*__ITEMS__*/", json.dumps(items, ensure_ascii=False, separators=(",", ":")).replace("</", "<\\/"))
            .replace("/*__META__*/", json.dumps(meta, ensure_ascii=False, separators=(",", ":")).replace("</", "<\\/")))
    html = (html.replace("__COUNT__", str(meta["count"]))
                .replace("__CATCOUNT__", str(len(cats)))
                .replace("__GENERATED__", meta["generated_at"])
                .replace("__HASH_SHORT__", meta["source_sha256"][:16]))
    out.write_text(html, encoding="utf-8", newline="\n")


def md_slug(s: str) -> str:
    s = str(s).lower()
    s = re.sub(r"[^a-z0-9 \-_]", "", s)
    return re.sub(r"\s", "-", s.strip())


def write_index_md(cats, items, meta, out: Path) -> None:
    lines = [
        "# Job Catalog — readable index",
        "",
        f"{meta['count']} sources in {len(cats)} categories. Generated {meta['generated_at']} "
        f"from `data.json` (sha256 `{meta['source_sha256'][:16]}…`).",
        "",
        "`data.json` itself is not changed. Country in names, filled subcategories and the "
        "`(view)` note are display-only. `(view)` means the subcategory was added in this "
        "index because the data file has none.",
        "",
        "## Contents",
        "",
    ]
    for c in cats:
        n = sum(1 for it in items if it["category"] == c["id"])
        lines.append(f"- [{md_text(c['name'])}](#{md_slug(c['name'])}) — {n}")

    for c in cats:
        citems = [it for it in items if it["category"] == c["id"]]
        if not citems:
            continue
        lines += ["", f"## {md_text(c['name'])}", "", f"{len(citems)} sources in this category.", ""]
        for sid, sname in c["subs"]:
            sitems = [it for it in citems if it["subcategory"] == sid]
            if not sitems:
                continue
            lines += [f"### {md_text(sname)} — {len(sitems)}", ""]
            for it in sitems:
                name = it["display_name"]
                if it["doubtful_subcategory"]:
                    name += " _(view)_"
                tags = [t for t in it["tags"]]
                suffix = f" — {', '.join(tags)}" if tags else ""
                lines.append(f"- [{md_text(name)}](<{it['url']}>){suffix}")
            lines.append("")
    out.write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8", newline="\n")


def write_mindmap(cats, items, meta, html_out: Path, md_out: Path) -> None:
    mmd = ["mindmap", f"  root((Job Catalog {meta['count']}))"]
    for c in cats:
        citems = [it for it in items if it["category"] == c["id"]]
        mmd.append(f"    {mermaid_text(c['name'])} {len(citems)}")
        for sid, sname in c["subs"]:
            n = sum(1 for it in citems if it["subcategory"] == sid)
            if n:
                mmd.append(f"      {mermaid_text(sname)} {n}")
    source = "\n".join(mmd) + "\n"

    flow = ["flowchart LR", f'  root(["Job Catalog - {meta["count"]} sources"])']
    for i, c in enumerate(cats):
        citems = [it for it in items if it["category"] == c["id"]]
        cid = f"c{i}"
        flow.append(f'  {cid}["{mermaid_text(c["name"])}<br/>{len(citems)}"]')
        flow.append(f"  root --> {cid}")
        for j, (sid, sname) in enumerate(c["subs"]):
            n = sum(1 for it in citems if it["subcategory"] == sid)
            if n:
                flow.append(f'  {cid}_{j}["{mermaid_text(sname)}<br/>{n}"]')
                flow.append(f"  {cid} --> {cid}_{j}")

    md_out.write_text(
        "# Job Catalog — mind map\n\n"
        f"All {meta['count']} sources as {len(cats)} categories and their subcategories.\n"
        "GitHub renders the tree below. Offline interactive version: `mindmap.html`.\n\n"
        "```mermaid\n" + "\n".join(flow) + "\n```\n",
        encoding="utf-8",
        newline="\n",
    )

    html = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Job Catalog — mind map</title>
<style>
  html,body{{margin:0;height:100%}}
  body{{font:14px/1.5 -apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,Arial,sans-serif;background:#f7f8fa;color:#17202a}}
  header{{padding:18px 24px;background:#fff;border-bottom:1px solid #e4e7ec}}
  h1{{font-size:20px;margin:0 0 4px}}
  p{{margin:0;color:#667085;font-size:13px}}
  #map{{height:calc(100vh - 86px);overflow:auto;padding:18px}}
  .err{{display:none;margin:24px;padding:16px;border:1px solid #fedf89;background:#fffaeb;border-radius:12px;color:#b54708}}
</style>
</head>
<body>
<header>
  <h1>Job Catalog — {meta['count']} sources, {len(cats)} categories</h1>
  <p>Mind map by category and subcategory. Works offline; the drawing library is in <code>vendor/mermaid.min.js</code>.</p>
</header>
<div id="map">
  <pre class="mermaid">{mmd_render(source)}</pre>
</div>
<div class="err" id="err">The map could not be drawn. Check that the file <code>vendor/mermaid.min.js</code> is next to this page, or open <code>mindmap.md</code>.</div>
<script src="vendor/mermaid.min.js"></script>
<script>
  if (window.mermaid) {{
    mermaid.initialize({{startOnLoad:true, securityLevel:'loose', theme:'base',
      themeVariables:{{fontFamily:'-apple-system,Segoe UI,Roboto,Arial,sans-serif', fontSize:'14px', primaryColor:'#eff4ff', primaryBorderColor:'#2563eb', lineColor:'#98a2b3'}},
      mindmap:{{padding:14, maxNodeWidth:280}}}});
  }} else {{
    document.getElementById('err').style.display='block';
  }}
</script>
</body>
</html>
"""
    html_out.write_text(html, encoding="utf-8", newline="\n")


def mmd_render(source: str) -> str:
    return esc_html(source)


def write_machine(cats, items, meta, out_dir: Path) -> dict:
    cat_counts = {c["id"]: sum(1 for it in items if it["category"] == c["id"]) for c in cats}
    sub_total = 0
    categories = []
    for c in cats:
        subs = []
        for sid, sname in c["subs"]:
            n = sum(1 for it in items if it["category"] == c["id"] and it["subcategory"] == sid)
            if n:
                subs.append({"id": sid, "name": sname, "count": n,
                             "source": "view" if c["id"] in EXTRA_SUBS else "data"})
        sub_total += len(subs)
        categories.append({"id": c["id"], "name": c["name"], "color": c["color"],
                           "count": cat_counts[c["id"]], "subcategories": subs})

    countries = {}
    regions = {}
    for it in items:
        for co in it["countries"]:
            countries[co] = countries.get(co, 0) + 1
        for rg in it["regions"]:
            regions[rg] = regions.get(rg, 0) + 1

    index = {
        "schema": SCHEMA_ID,
        "generated_at": meta["generated_at"],
        "source": {
            "path": "data.json",
            "sha256": meta["source_sha256"],
            "bytes": meta["source_bytes"],
            "items": meta["count"],
            "categories": len(cats),
            "modified": False,
        },
        "counts": {
            "items": len(items),
            "categories": len(cats),
            "subcategories": sub_total,
            "items_with_country": sum(1 for it in items if it["countries"]),
            "remote_items": sum(1 for it in items if it["remote"]),
            "items_without_subcategory_in_source": sum(1 for it in items if it["subcategory_source"] == "view"),
            "doubtful_subcategories": sum(1 for it in items if it["doubtful_subcategory"]),
        },
        "categories": categories,
        "countries": [{"name": k, "count": v} for k, v in sorted(countries.items(), key=lambda x: (-x[1], x[0]))],
        "regions": [{"name": r, "count": regions.get(r, 0)} for r in REGIONS],
    }
    (out_dir / "index.json").write_text(json.dumps(index, ensure_ascii=False, indent=2) + "\n", encoding="utf-8", newline="\n")

    with (out_dir / "index.jsonl").open("w", encoding="utf-8", newline="\n") as f:
        for it in items:
            rec = {
                "schema": SCHEMA_ID + ".item",
                "id": it["id"],
                "name": it["name"],
                "display_name": it["display_name"],
                "url": it["url"],
                "category": it["category"],
                "category_name": it["category_name"],
                "subcategory": it["subcategory"],
                "subcategory_name": it["subcategory_name"],
                "subcategory_source": it["subcategory_source"],
                "doubtful_subcategory": it["doubtful_subcategory"],
                "tags": it["tags"],
                "countries": it["countries"],
                "regions": it["regions"],
                "remote": it["remote"],
                "login": it["login"],
                "freelance": it["freelance"],
            }
            f.write(json.dumps(rec, ensure_ascii=False, separators=(",", ":")) + "\n")

    schema = {
        "schema": SCHEMA_ID,
        "about": "Machine-readable index of the Job Catalog, generated from data.json. "
                 "data.json is the source of truth and is not modified.",
        "files": {
            "data.json": "Original catalog: {cats: [...], items: [{id,name,url,tags,cat,sub}]}.",
            "index.json": "Summary: schema id, generation time, source hash, counts, categories with subcategories, countries, regions.",
            "index.jsonl": "One catalog source per line (JSON object, same item fields as below).",
            "schema.json": "This file.",
            "catalog.html": "Human page, works offline, search and filters.",
            "INDEX.md": "Human-readable Markdown index.",
            "mindmap.html": "Offline mind map of categories and subcategories (Mermaid).",
        },
        "index_json_fields": {
            "schema": "Format id: job-catalog-index-v1.",
            "generated_at": "UTC time of generation.",
            "source": "Path, sha256, size and counts of data.json; modified=false means data.json was only read.",
            "counts": "Totals: items, categories, subcategories, items_with_country, remote_items, items_without_subcategory_in_source, doubtful_subcategories.",
            "categories": "Array of {id, name, color, count, subcategories:[{id,name,count,source}]}; source=data means the subcategory exists in data.json, source=view means it was added for display.",
            "countries": "Array of {name, count}, derived from tags.",
            "regions": "Array of {name, count}, derived from country tags and the Remote/EU tags.",
        },
        "item_fields": {
            "schema": "job-catalog-index-v1.item",
            "id": "Original id from data.json.",
            "name": "Original name from data.json.",
            "display_name": "Name plus country for humans; display-only.",
            "url": "Original URL.",
            "category": "Original category id (data.json cat).",
            "category_name": "Human category name.",
            "subcategory": "Original sub id, or a view-only id when data.json has none.",
            "subcategory_name": "Human subcategory name.",
            "subcategory_source": "data = present in data.json; view = added for display only.",
            "doubtful_subcategory": "true when the subcategory was added in the view, not present in data.json.",
            "tags": "Original tags array.",
            "countries": "Countries derived from tags (display-only).",
            "regions": "Regions derived from country tags and Remote/EU (display-only).",
            "remote": "true when the Remote tag is present.",
            "login": "true when the Login tag is present.",
            "freelance": "true when the Freelance tag is present.",
        },
        "notes": [
            "Every source from data.json is present; nothing is dropped.",
            "Display-only fields never change data.json; consumers that need the original data should read data.json.",
            "URLs are kept exactly as in data.json (only trimmed of surrounding spaces).",
        ],
    }
    (out_dir / "schema.json").write_text(json.dumps(schema, ensure_ascii=False, indent=2) + "\n", encoding="utf-8", newline="\n")
    return index


def scan_forbidden(files: list[Path]) -> None:
    hits = []
    for p in files:
        text = p.read_text(encoding="utf-8", errors="replace")
        for rx in FORBIDDEN:
            if rx.search(text):
                hits.append(f"{p.name}: {rx.pattern}")
    if hits:
        raise SystemExit("Forbidden personal/secret pattern found:\n" + "\n".join(hits))


def main() -> int:
    data = json.loads(DATA_PATH.read_text(encoding="utf-8"))
    src_hash = sha256(DATA_PATH)
    cats, _ = build_cats(data)
    items = enrich(data, cats)

    tag_counts = {}
    country_counts = {}
    for it in items:
        for co in it["countries"]:
            country_counts[co] = country_counts.get(co, 0) + 1
    meta = {
        "count": len(items),
        "generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC"),
        "source_sha256": src_hash,
        "source_bytes": DATA_PATH.stat().st_size,
        "countries": [{"name": k, "count": v} for k, v in sorted(country_counts.items(), key=lambda x: (-x[1], x[0]))],
        "regions": REGIONS,
    }

    write_page(cats, items, meta, ROOT / "catalog.html")
    write_index_md(cats, items, meta, ROOT / "INDEX.md")
    (ROOT / "vendor").mkdir(exist_ok=True)
    write_mindmap(cats, items, meta, ROOT / "mindmap.html", ROOT / "mindmap.md")
    index = write_machine(cats, items, meta, ROOT)

    outputs = [ROOT / n for n in ("catalog.html", "INDEX.md", "mindmap.html", "mindmap.md",
                                  "index.json", "index.jsonl", "schema.json")]
    scan_forbidden(outputs)

    # validation: every original URL must be present in both machine files
    src_urls = {it["url"] for it in data["items"]}
    idx_urls = {it["url"] for it in items}
    jsonl_urls = {json.loads(line)["url"] for line in (ROOT / "index.jsonl").read_text(encoding="utf-8").splitlines()}
    if not (len(src_urls) == len(idx_urls) == len(jsonl_urls)) or src_urls != idx_urls or src_urls != jsonl_urls:
        raise SystemExit(f"URL mismatch: source={len(src_urls)} view={len(idx_urls)} jsonl={len(jsonl_urls)}")
    for name in ("index.json", "schema.json"):
        json.loads((ROOT / name).read_text(encoding="utf-8"))
    page = (ROOT / "catalog.html").read_text(encoding="utf-8")
    if "const ITEMS =" not in page or data["items"][0]["url"] not in page:
        raise SystemExit("catalog.html looks empty")

    print(f"OK data.json sha256={src_hash}")
    print(f"OK {len(items)} items, {len(cats)} categories, {index['counts']['subcategories']} subcategories")
    print(f"OK doubtful={index['counts']['doubtful_subcategories']} countries={len(country_counts)} remote={index['counts']['remote_items']}")
    for p in outputs:
        print(f"  {p.name:14s} {p.stat().st_size:>8} bytes")
    return 0


if __name__ == "__main__":
    sys.exit(main())
