#!/usr/bin/env python3
"""Job Catalog API v2 — catalog + workspace (profile, applications, queue, stats)."""
import json, os, re, threading, time
from flask import Flask, jsonify, request, send_from_directory

app = Flask(__name__)
BASE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(BASE)
DATA = os.path.join(ROOT, "data.json")
WS = os.path.join(BASE, "workspace.json")
LOCK = threading.Lock()

CATALOG_RE = re.compile(r"^[a-zA-Z0-9_.-]{1,64}$")
RESUME_DIR = os.path.join(BASE, "resumes")

WS_DEFAULT = {
    "profile": {
        "name": "",
        "email": "",
        "location": "",
        "work_hours": "",
        "target_levels": [],
        "notes": "",
        "updated": None,
    },
    "applications": [],
    "queue": [],
    "submissions": [],
    "stats": {"swept": 0, "filtered_out": 0, "queued": 0, "applied": 0, "errors": 0},
}


def read(path, default):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return json.loads(json.dumps(default))


def write(path, d):
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(d, f, ensure_ascii=False, indent=2)
    os.replace(tmp, path)


def ws_all():
    return read(WS, WS_DEFAULT)


def ws_save(d):
    write(WS, d)


# ---------- readable views (generated from data.json) ----------

VIEW_FILES = {
    "catalog.html", "INDEX.md", "mindmap.html", "mindmap.md",
    "index.json", "index.jsonl", "schema.json",
}


@app.route("/")
def home():
    return send_from_directory(ROOT, "catalog.html")


@app.route("/<path:name>")
def view_file(name):
    if name in VIEW_FILES or (name.startswith("vendor/") and name.count("/") == 1):
        return send_from_directory(ROOT, name)
    return jsonify({"ok": False, "error": "not found"}), 404


# ---------- catalog ----------

@app.route("/api/data")
def get_data():
    return jsonify(read(DATA, {"cats": [], "items": []}))


@app.route("/api/data", methods=["POST"])
def post_data():
    d = request.json
    if not isinstance(d, dict) or not isinstance(d.get("cats"), list) or not isinstance(d.get("items"), list):
        return jsonify({"ok": False, "error": "bad shape"}), 400
    with LOCK:
        write(DATA, d)
    return jsonify({"ok": True})


# ---------- workspace ----------

@app.route("/api/ws")
def get_ws():
    return jsonify(ws_all())


@app.route("/api/ws/profile", methods=["POST"])
def post_profile():
    p = request.json
    if not isinstance(p, dict):
        return jsonify({"ok": False}), 400
    with LOCK:
        d = ws_all()
        merged = dict(WS_DEFAULT["profile"])
        merged.update(p)
        merged["updated"] = int(time.time())
        d["profile"] = merged
        ws_save(d)
    return jsonify({"ok": True, "profile": d["profile"]})


def _norm_company(s):
    return re.sub(r"[^a-z0-9]+", " ", str(s or "").lower()).strip()


@app.route("/api/ws/applications", methods=["POST"])
def post_application():
    a = request.json
    if not isinstance(a, dict) or not a.get("company") or not a.get("role"):
        return jsonify({"ok": False, "error": "company and role required"}), 400
    with LOCK:
        d = ws_all()
        key = _norm_company(a.get("company"))
        if a.get("id"):
            for i, ex in enumerate(d["applications"]):
                if ex.get("id") == a["id"]:
                    a["edited"] = int(time.time())
                    d["applications"][i] = {**ex, **a}
                    ws_save(d)
                    return jsonify({"ok": True, "app": d["applications"][i]})
        dup = [x for x in d["applications"] if _norm_company(x.get("company")) == key]
        limit = 2
        if dup and len(dup) >= limit and str(a.get("company", "")).strip() and \
           _norm_company(a.get("company")) == _norm_company(dup[-1].get("company")) and not a.get("force"):
            pass  # 1 company = max 2 applications; allow but warn below
        rec = {
            "id": a.get("id") or "a" + str(int(time.time() * 1000))[-11:] + str(len(d["applications"])),
            "company": str(a["company"]).strip()[:120],
            "role": str(a["role"]).strip()[:160],
            "status": a.get("status", "applied"),
            "source": a.get("source", ""),
            "url": a.get("url", ""),
            "resume": a.get("resume", ""),
            "level": a.get("level", ""),
            "notes": a.get("notes", ""),
            "date": a.get("date") or time.strftime("%Y-%m-%d"),
            "ts": int(time.time()),
        }
        d["applications"].append(rec)
        d["stats"]["applied"] = len(d["applications"])
        warn = len(dup) + 1 > limit
        ws_save(d)
    return jsonify({"ok": True, "app": rec, "dup_warn": warn})


@app.route("/api/ws/applications/<app_id>", methods=["DELETE"])
def del_application(app_id):
    with LOCK:
        d = ws_all()
        before = len(d["applications"])
        d["applications"] = [x for x in d["applications"] if x.get("id") != app_id]
        d["stats"]["applied"] = len(d["applications"])
        ws_save(d)
        return jsonify({"ok": True, "deleted": before - len(d["applications"])})


@app.route("/api/ws/applications/bulk", methods=["POST"])
def bulk_applications():
    """Append-only bulk import from pipeline. Body: {"apps":[...]}"""
    body = request.json
    apps = body.get("apps") if isinstance(body, dict) else body
    if not isinstance(apps, list):
        return jsonify({"ok": False, "error": "apps[] required"}), 400
    added = 0
    with LOCK:
        d = ws_all()
        for a in apps:
            if not isinstance(a, dict) or not a.get("company") or not a.get("role"):
                continue
            if any(x.get("company", "").lower() == str(a["company"]).lower() and
                   x.get("role", "").lower() == str(a["role"]).lower()
                   for x in d["applications"]):
                continue
            d["applications"].append({
                "id": "a" + str(int(time.time() * 1000))[-11:] + str(len(d["applications"])),
                "company": str(a["company"]).strip()[:120],
                "role": str(a["role"]).strip()[:160],
                "status": a.get("status", "applied"),
                "source": a.get("source", ""),
                "url": a.get("url", ""),
                "resume": a.get("resume", ""),
                "level": a.get("level", ""),
                "notes": a.get("notes", ""),
                "date": a.get("date") or time.strftime("%Y-%m-%d"),
                "ts": int(time.time()),
            })
            added += 1
        d["stats"]["applied"] = len(d["applications"])
        ws_save(d)
    return jsonify({"ok": True, "added": added})


@app.route("/api/ws/queue", methods=["POST"])
def post_queue():
    q = request.json
    if not isinstance(q, dict):
        return jsonify({"ok": False}), 400
    if "done" in q:  # mark done / remove
        with LOCK:
            d = ws_all()
            d["queue"] = [x for x in d["queue"] if x.get("id") != q["done"]]
            ws_save(d)
        return jsonify({"ok": True})
    if not q.get("title"):
        return jsonify({"ok": False, "error": "title required"}), 400
    with LOCK:
        d = ws_all()
        rec = {
            "id": "q" + str(int(time.time() * 1000))[-11:] + str(len(d["queue"])),
            "title": str(q["title"]).strip()[:200],
            "url": q.get("url", ""),
            "reason": q.get("reason", ""),
            "ts": int(time.time()),
        }
        d["queue"].append(rec)
        ws_save(d)
    return jsonify({"ok": True, "item": rec})


@app.route("/api/ws/stats", methods=["POST"])
def post_stats():
    s = request.json
    if not isinstance(s, dict):
        return jsonify({"ok": False}), 400
    with LOCK:
        d = ws_all()
        for k in d["stats"]:
            if k in s:
                d["stats"][k] = int(s[k])
        ws_save(d)
    return jsonify({"ok": True, "stats": d["stats"]})


# ---------- resumes ----------

@app.route("/api/resumes")
def get_resumes():
    try:
        files = sorted(f for f in os.listdir(RESUME_DIR) if f.lower().endswith(".pdf"))
    except OSError:
        files = []
    return jsonify([{"file": f, "url": "/resumes/" + f} for f in files])


# ---------- submissions (user drops a job URL -> bot fills it) ----------

@app.route("/api/ws/submit", methods=["POST"])
def post_submit():
    s = request.json
    if not isinstance(s, dict) or not s.get("url"):
        return jsonify({"ok": False, "error": "url required"}), 400
    action = s.get("action")
    with LOCK:
        d = ws_all()
        if action == "resolve":
            sid = s.get("id")
            d["submissions"] = [x for x in d["submissions"] if x.get("id") != sid]
            ws_save(d)
            return jsonify({"ok": True})
        rec = {
            "id": "s" + str(int(time.time() * 1000))[-11:] + str(len(d["submissions"])),
            "url": str(s["url"]).strip()[:500],
            "resume": s.get("resume", ""),
            "notes": s.get("notes", ""),
            "status": "new",  # new -> working -> done | failed
            "result": "",
            "ts": int(time.time()),
        }
        if any(x.get("url") == rec["url"] and x.get("status") in ("new", "working") for x in d["submissions"]):
            return jsonify({"ok": False, "error": "already queued"}), 409
        d["submissions"].append(rec)
        ws_save(d)
    return jsonify({"ok": True, "sub": rec})


@app.route("/api/ws/submit/<sid>", methods=["POST"])
def update_submit(sid):
    """Bot-side status updates: {"status":"working|done|failed","result":"..."}
       On done -> optionally auto-create application record."""
    b = request.json
    with LOCK:
        d = ws_all()
        for x in d["submissions"]:
            if x.get("id") == sid:
                x["status"] = b.get("status", x["status"])
                x["result"] = b.get("result", x.get("result", ""))
                if b.get("application"):
                    a = b["application"]
                    d["applications"].append({
                        "id": "a" + str(int(time.time() * 1000))[-11:] + str(len(d["applications"])),
                        "company": str(a.get("company", "?")).strip()[:120],
                        "role": str(a.get("role", "?")).strip()[:160],
                        "status": "applied",
                        "source": a.get("source", "bot"),
                        "url": x["url"],
                        "resume": a.get("resume", x.get("resume", "")),
                        "level": a.get("level", ""),
                        "notes": a.get("notes", ""),
                        "date": time.strftime("%Y-%m-%d"),
                        "ts": int(time.time()),
                    })
                    d["stats"]["applied"] = len(d["applications"])
                ws_save(d)
                return jsonify({"ok": True, "sub": x})
    return jsonify({"ok": False, "error": "not found"}), 404


@app.route("/api/ws/submit/next", methods=["GET"])
def next_submit():
    """Bot polls this: takes the oldest 'new' submission, marks it working."""
    with LOCK:
        d = ws_all()
        for x in d["submissions"]:
            if x.get("status") == "new":
                x["status"] = "working"
                ws_save(d)
                return jsonify({"ok": True, "sub": x})
    return jsonify({"ok": True, "sub": None})


if __name__ == "__main__":
    host = os.environ.get("JOB_CATALOG_HOST", "127.0.0.1")
    port = int(os.environ.get("JOB_CATALOG_PORT", "5056"))
    app.run(host=host, port=port)
