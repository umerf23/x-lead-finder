"""
Step 9 web application.

Purpose: put a small local web server behind the dashboard so that it
stops being a static page and starts behaving like a product.

What this adds over build_dashboard.py:
  - Marking a lead as saved or handled is remembered permanently
  - A settings page where watchlists are edited in the browser,
    so nobody ever has to open config.yaml in Notepad
  - A Collect now button that runs collect.py, with the cost shown
    and confirmed before anything is spent
  - A Score now button that runs score.py, which is always free

Where things are stored:
  data/posts.json    collected posts, written by collect.py
  data/scored.json   judged posts, written by score.py
  data/status.json   your saved and handled marks, written by this app

Status lives in its own file on purpose. That way running
score.py --fresh can throw away every score and rebuild it without
losing a single one of your review marks.

Run it with:

    python app.py

Then open http://127.0.0.1:8000 in your browser.

It listens on 127.0.0.1, which means this computer only. Nothing is
exposed to the internet.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import threading
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

try:
    import yaml
    from fastapi import FastAPI, HTTPException
    from fastapi.responses import HTMLResponse, JSONResponse
    from pydantic import BaseModel, Field
except ImportError as missing:
    print("A required package is not installed:", missing.name)
    print("")
    print("With (venv) showing in PowerShell, run this line:")
    print("")
    print("    pip install fastapi uvicorn pyyaml")
    print("")
    raise SystemExit(1)

# ---------------------------------------------------------------------
# Where everything lives
# ---------------------------------------------------------------------

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_FOLDER = os.path.join(BASE_DIR, "data")

CONFIG_FILE = os.path.join(BASE_DIR, "config.yaml")
CONFIG_BACKUP = os.path.join(BASE_DIR, "config.backup.yaml")
POSTS_FILE = os.path.join(DATA_FOLDER, "posts.json")
SCORED_FILE = os.path.join(DATA_FOLDER, "scored.json")
STATUS_FILE = os.path.join(DATA_FOLDER, "status.json")
PAGE_FILE = os.path.join(BASE_DIR, "dashboard_app.html")

VALID_STATUSES = ("new", "saved", "handled")

# Only one collector or scorer may run at a time. Two at once would
# fight over the same files and could spend twice.
RUN_LOCK = threading.Lock()

app = FastAPI(title="X Lead Finder", docs_url=None, redoc_url=None)


# ---------------------------------------------------------------------
# Small helpers for reading and writing files
# ---------------------------------------------------------------------

def read_json(path: str, fallback: Any) -> Any:
    """Read a JSON file, returning the fallback if it is missing or broken."""
    if not os.path.exists(path):
        return fallback
    try:
        with open(path, "r", encoding="utf-8") as handle:
            return json.load(handle)
    except (ValueError, OSError):
        return fallback


def write_json(path: str, content: Any) -> None:
    """Write JSON safely, so a crash mid-write cannot corrupt the file."""
    os.makedirs(os.path.dirname(path), exist_ok=True)
    temporary = path + ".tmp"
    with open(temporary, "w", encoding="utf-8") as handle:
        json.dump(content, handle, indent=2, ensure_ascii=False)
    os.replace(temporary, path)


def read_config() -> Dict[str, Any]:
    if not os.path.exists(CONFIG_FILE):
        raise HTTPException(
            status_code=500,
            detail="config.yaml is missing from the project folder.")
    try:
        with open(CONFIG_FILE, "r", encoding="utf-8") as handle:
            config = yaml.safe_load(handle)
    except yaml.YAMLError as error:
        raise HTTPException(
            status_code=500,
            detail="config.yaml could not be read. Details: " + str(error))
    if not isinstance(config, dict):
        raise HTTPException(status_code=500, detail="config.yaml is empty.")
    return config


def whole_number(value: Any, fallback: int, lowest: int = 1) -> int:
    try:
        return max(lowest, int(value))
    except (TypeError, ValueError):
        return fallback


# ---------------------------------------------------------------------
# Writing config.yaml back out, comments and all
# ---------------------------------------------------------------------

CONFIG_HEADER = """\
# =====================================================================
#  SETTINGS FILE
#  You can edit this in the browser instead. Open the app and click
#  Settings. Editing here by hand still works, but the browser will
#  not mangle your spacing and Notepad will.
#
#  Rules for editing by hand:
#    - Keep the spacing exactly as it is. Indentation matters here.
#    - Text with special characters goes inside single quotes.
#    - Lines starting with # are notes and are ignored by the program.
#
#  Query building blocks you can reuse:
#    "exact phrase"      matches those words together
#    OR                  matches either side
#    lang:en             English posts only
#    -filter:retweets    exclude reposts
#    -filter:replies     exclude replies
#
#  Last saved from the app: {stamp}
# =====================================================================

"""


def write_config(config: Dict[str, Any]) -> None:
    """Save config.yaml, keeping a backup of the previous version."""
    if os.path.exists(CONFIG_FILE):
        shutil.copyfile(CONFIG_FILE, CONFIG_BACKUP)

    stamp = datetime.now().strftime("%d %B %Y at %H:%M")
    body = yaml.safe_dump(
        config,
        sort_keys=False,
        allow_unicode=True,
        default_flow_style=False,
        width=100000,          # never wrap a search query onto two lines
    )

    temporary = CONFIG_FILE + ".tmp"
    with open(temporary, "w", encoding="utf-8") as handle:
        handle.write(CONFIG_HEADER.format(stamp=stamp))
        handle.write(body)
    os.replace(temporary, CONFIG_FILE)


# ---------------------------------------------------------------------
# Shapes of the data the browser sends us
# ---------------------------------------------------------------------

class StatusChange(BaseModel):
    status: str = Field(description="one of new, saved, handled")


class Watchlist(BaseModel):
    name: str
    description: str = ""
    query: str
    enabled: bool = True


class Settings(BaseModel):
    max_posts_per_run: int = 40
    max_posts_per_watchlist: int = 20
    max_posts_per_author: int = 2
    max_pages_per_watchlist: int = 3
    cost_per_1000_posts: float = 0.15
    confirm_before_spending: bool = True
    watchlists: List[Watchlist] = []


# ---------------------------------------------------------------------
# Leads
# ---------------------------------------------------------------------

def load_leads() -> List[Dict[str, Any]]:
    """Join the scored posts to your review marks."""
    scored = read_json(SCORED_FILE, [])
    if not isinstance(scored, list):
        scored = []

    statuses = read_json(STATUS_FILE, {})
    if not isinstance(statuses, dict):
        statuses = {}

    leads = []
    for row in scored:
        if not isinstance(row, dict):
            continue
        post_id = str(row.get("post_id", ""))
        if not post_id:
            continue
        mark = statuses.get(post_id) or {}
        lead = dict(row)
        lead["post_id"] = post_id
        lead["status"] = mark.get("status", "new")
        lead["status_changed_at"] = mark.get("changed_at", "")
        leads.append(lead)

    leads.sort(key=lambda item: item.get("intent_score", 0), reverse=True)
    return leads


@app.get("/api/leads")
def get_leads() -> JSONResponse:
    leads = load_leads()
    collected = read_json(POSTS_FILE, [])
    collected_count = len(collected) if isinstance(collected, list) else 0

    return JSONResponse({
        "leads": leads,
        "totals": {
            "collected": collected_count,
            "scored": len(leads),
            "unscored": max(0, collected_count - len(leads)),
            "strong": sum(1 for lead in leads
                          if lead.get("intent_score", 0) >= 65),
            "handled": sum(1 for lead in leads
                           if lead.get("status") == "handled"),
            "saved": sum(1 for lead in leads if lead.get("status") == "saved"),
        },
    })


@app.post("/api/leads/{post_id}/status")
def set_status(post_id: str, change: StatusChange) -> JSONResponse:
    if change.status not in VALID_STATUSES:
        raise HTTPException(
            status_code=400,
            detail="Status must be one of: " + ", ".join(VALID_STATUSES))

    statuses = read_json(STATUS_FILE, {})
    if not isinstance(statuses, dict):
        statuses = {}

    if change.status == "new":
        statuses.pop(post_id, None)
    else:
        statuses[post_id] = {
            "status": change.status,
            "changed_at": datetime.now(timezone.utc).isoformat(
                timespec="seconds"),
        }

    write_json(STATUS_FILE, statuses)
    return JSONResponse({"post_id": post_id, "status": change.status})


# ---------------------------------------------------------------------
# Settings
# ---------------------------------------------------------------------

@app.get("/api/settings")
def get_settings() -> JSONResponse:
    config = read_config()
    watchlists = []
    for item in config.get("watchlists") or []:
        if not isinstance(item, dict):
            continue
        watchlists.append({
            "name": str(item.get("name", "")).strip(),
            "description": str(item.get("description", "") or "").strip(),
            "query": str(item.get("query", "") or "").strip(),
            "enabled": bool(item.get("enabled", False)),
        })

    return JSONResponse({
        "max_posts_per_run": whole_number(config.get("max_posts_per_run"), 40),
        "max_posts_per_watchlist": whole_number(
            config.get("max_posts_per_watchlist"), 20),
        "max_posts_per_author": whole_number(
            config.get("max_posts_per_author"), 2),
        "max_pages_per_watchlist": whole_number(
            config.get("max_pages_per_watchlist"), 3),
        "cost_per_1000_posts": float(
            config.get("cost_per_1000_posts", 0.15) or 0.15),
        "confirm_before_spending": bool(
            config.get("confirm_before_spending", True)),
        "watchlists": watchlists,
    })


@app.post("/api/settings")
def save_settings(settings: Settings) -> JSONResponse:
    cleaned: List[Dict[str, Any]] = []

    for index, watchlist in enumerate(settings.watchlists, start=1):
        name = watchlist.name.strip()
        query = watchlist.query.strip()

        if not name:
            raise HTTPException(
                status_code=400,
                detail=f"Watchlist {index} needs a name.")
        if not query:
            raise HTTPException(
                status_code=400,
                detail=f"Watchlist '{name}' needs a search query.")

        cleaned.append({
            "name": name,
            "description": watchlist.description.strip(),
            "query": query,
            "enabled": bool(watchlist.enabled),
        })

    names = [item["name"].lower() for item in cleaned]
    duplicates = {name for name in names if names.count(name) > 1}
    if duplicates:
        raise HTTPException(
            status_code=400,
            detail="Two watchlists share the name: " + ", ".join(duplicates))

    config = {
        "max_posts_per_run": max(1, settings.max_posts_per_run),
        "max_posts_per_watchlist": max(1, settings.max_posts_per_watchlist),
        "max_posts_per_author": max(1, settings.max_posts_per_author),
        "max_pages_per_watchlist": max(1, settings.max_pages_per_watchlist),
        "cost_per_1000_posts": max(0.0, settings.cost_per_1000_posts),
        "confirm_before_spending": bool(settings.confirm_before_spending),
        "watchlists": cleaned,
    }

    write_config(config)
    return JSONResponse({
        "saved": True,
        "watchlists": len(cleaned),
        "enabled": sum(1 for item in cleaned if item["enabled"]),
    })


@app.get("/api/estimate")
def get_estimate() -> JSONResponse:
    """What the next collection run could cost, at worst."""
    config = read_config()
    per_run = whole_number(config.get("max_posts_per_run"), 40)
    price = float(config.get("cost_per_1000_posts", 0.15) or 0.15)
    active = [w for w in (config.get("watchlists") or [])
              if isinstance(w, dict) and w.get("enabled")]

    return JSONResponse({
        "max_posts": per_run,
        "price_per_1000": price,
        "worst_case_cost": round(per_run / 1000.0 * price, 4),
        "active_watchlists": [w.get("name", "unnamed") for w in active],
    })


# ---------------------------------------------------------------------
# Running the collector and the scorer
# ---------------------------------------------------------------------

def run_script(script: str, arguments: List[str],
               answer_yes: bool) -> Dict[str, Any]:
    """
    Run one of the project scripts and hand back everything it printed.

    collect.py asks for a typed yes before spending. The browser has
    already asked you to confirm, so we feed that yes in here rather
    than leaving the script waiting forever for a keyboard.
    """
    path = os.path.join(BASE_DIR, script)
    if not os.path.exists(path):
        return {"ok": False, "output": f"{script} is not in the project folder."}

    if not RUN_LOCK.acquire(blocking=False):
        return {"ok": False,
                "output": "Something is already running. Wait for it to finish."}

    try:
        result = subprocess.run(
            [sys.executable, path] + arguments,
            cwd=BASE_DIR,
            input="yes\n" if answer_yes else "",
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=600,
        )
        output = (result.stdout or "") + (result.stderr or "")
        return {"ok": result.returncode == 0, "output": output.strip()}
    except subprocess.TimeoutExpired:
        return {"ok": False,
                "output": f"{script} took longer than ten minutes and was stopped."}
    except OSError as error:
        return {"ok": False, "output": f"Could not start {script}: {error}"}
    finally:
        RUN_LOCK.release()


@app.post("/api/run/collect")
def run_collect() -> JSONResponse:
    return JSONResponse(run_script("collect.py", [], answer_yes=True))


@app.post("/api/run/score")
def run_score() -> JSONResponse:
    return JSONResponse(run_script("score.py", [], answer_yes=False))


@app.post("/api/run/rescore")
def run_rescore() -> JSONResponse:
    return JSONResponse(run_script("score.py", ["--fresh"], answer_yes=False))


# ---------------------------------------------------------------------
# The page itself
# ---------------------------------------------------------------------

@app.get("/", response_class=HTMLResponse)
def home() -> HTMLResponse:
    if not os.path.exists(PAGE_FILE):
        return HTMLResponse(
            "<h1>dashboard_app.html is missing</h1>"
            "<p>It must sit in the same folder as app.py.</p>",
            status_code=500)
    with open(PAGE_FILE, "r", encoding="utf-8") as handle:
        return HTMLResponse(handle.read())


# ---------------------------------------------------------------------
# Starting up
# ---------------------------------------------------------------------

def main() -> None:
    try:
        import uvicorn
    except ImportError:
        print("uvicorn is not installed.")
        print("")
        print("With (venv) showing in PowerShell, run this line:")
        print("")
        print("    pip install fastapi uvicorn pyyaml")
        raise SystemExit(1)

    os.makedirs(DATA_FOLDER, exist_ok=True)

    print("")
    print("=" * 62)
    print("X LEAD FINDER")
    print("=" * 62)
    print("  Open this address in your browser:")
    print("")
    print("      http://127.0.0.1:8000")
    print("")
    print("  This computer only. Nothing is exposed to the internet.")
    print("  Press Ctrl and C in this window to stop the app.")
    print("=" * 62)
    print("")

    uvicorn.run(app, host="127.0.0.1", port=8000, log_level="warning")


if __name__ == "__main__":
    main()
