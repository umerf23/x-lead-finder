"""
Step 7 dashboard builder.

Purpose: turn data/scored.json into a single web page you can open in
any browser, with filtering, sorting, and one click access to posts.

It costs nothing to run, needs no internet, and can be run as often
as you like.

Run it with:

    python build_dashboard.py

Then open the file it creates: dashboard.html
"""

import html
import json
import os
import webbrowser
from datetime import datetime

DATA_FOLDER = "data"
SCORED_FILE = os.path.join(DATA_FOLDER, "scored.json")
OUTPUT_FILE = "dashboard.html"

STRONG = 80
GOOD = 65
MAYBE = 40


def stop(message):
    print(message)
    raise SystemExit(1)


def load_scored():
    if not os.path.exists(SCORED_FILE):
        stop("Could not find " + SCORED_FILE + "\n"
             "Run collect.py and then score.py first.")

    try:
        with open(SCORED_FILE, "r", encoding="utf-8") as handle:
            rows = json.load(handle)
    except (ValueError, OSError) as error:
        stop("Could not read the scored file. Details: " + str(error))

    if not isinstance(rows, list) or not rows:
        stop("The scored file is empty. Run score.py first.")

    return rows


def clean(rows):
    """Sort by score, and mark repeat authors so one person cannot flood."""
    ranked = sorted(rows,
                    key=lambda r: (r.get("intent_score", 0),
                                   r.get("posted_at", "")),
                    reverse=True)

    seen_authors = set()
    prepared = []

    for row in ranked:
        author = (row.get("author") or "").lower()
        is_repeat = author in seen_authors and author != ""
        seen_authors.add(author)

        prepared.append({
            "post_id": row.get("post_id", ""),
            "author": row.get("author", ""),
            "author_name": row.get("author_name", ""),
            "followers": row.get("followers", 0) or 0,
            "text": row.get("text", "") or "",
            "post_url": row.get("post_url", "") or "",
            "posted_at": row.get("posted_at", "") or "",
            "watchlist": row.get("watchlist", "") or "",
            "score": int(row.get("intent_score", 0) or 0),
            "category": row.get("category", "") or "unsorted",
            "reason": row.get("reason", "") or "",
            "budget": bool(row.get("budget_signal", False)),
            "repeat": is_repeat,
        })

    return prepared


PAGE_TEMPLATE = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Lead Finder</title>
<style>
  :root {
    --ink: #14181f;
    --muted: #6b7280;
    --line: #e3e6ea;
    --bg: #f6f7f9;
    --card: #ffffff;
    --strong: #0f7b42;
  }
  * { box-sizing: border-box; }
  body {
    margin: 0;
    background: var(--bg);
    color: var(--ink);
    font-family: -apple-system, "Segoe UI", Roboto, Arial, sans-serif;
    font-size: 15px;
    line-height: 1.55;
  }
  header {
    background: var(--card);
    border-bottom: 1px solid var(--line);
    padding: 20px 28px;
  }
  h1 { margin: 0 0 4px; font-size: 20px; font-weight: 600; }
  .sub { color: var(--muted); font-size: 13px; }
  .stats { display: flex; gap: 28px; margin-top: 16px; flex-wrap: wrap; }
  .stat b { display: block; font-size: 22px; font-weight: 600; }
  .stat span { color: var(--muted); font-size: 12px; text-transform: uppercase; letter-spacing: .04em; }
  .controls {
    background: var(--card);
    border-bottom: 1px solid var(--line);
    padding: 14px 28px;
    display: flex;
    gap: 10px;
    flex-wrap: wrap;
    align-items: center;
    position: sticky;
    top: 0;
    z-index: 5;
  }
  select, input[type=text] {
    padding: 7px 10px;
    border: 1px solid var(--line);
    border-radius: 6px;
    font-size: 14px;
    background: #fff;
    color: var(--ink);
  }
  input[type=text] { min-width: 220px; }
  label.inline { font-size: 13px; color: var(--muted); display: flex; gap: 6px; align-items: center; }
  main { padding: 22px 28px 60px; max-width: 900px; }
  .card {
    background: var(--card);
    border: 1px solid var(--line);
    border-radius: 10px;
    padding: 16px 18px;
    margin-bottom: 12px;
  }
  .card.done { opacity: .45; }
  .row1 { display: flex; align-items: center; gap: 10px; flex-wrap: wrap; margin-bottom: 8px; }
  .score {
    font-weight: 600;
    font-size: 13px;
    padding: 3px 9px;
    border-radius: 20px;
    border: 1px solid transparent;
  }
  .s-strong { background: #e7f6ec; color: #12683a; border-color: #bfe5cd; }
  .s-good   { background: #fdf3dc; color: #8a5d05; border-color: #f2ddab; }
  .s-maybe  { background: #eef1f5; color: #4b5563; border-color: #dde2e9; }
  .s-low    { background: #f4f5f7; color: #9099a6; border-color: #e6e9ee; }
  .who { font-weight: 600; }
  .who a { color: inherit; text-decoration: none; }
  .who a:hover { text-decoration: underline; }
  .tag {
    font-size: 12px; color: var(--muted);
    border: 1px solid var(--line); border-radius: 5px; padding: 2px 7px;
  }
  .tag.money { background: #e7f6ec; color: #12683a; border-color: #bfe5cd; }
  .tag.repeat { background: #fdeeee; color: #97302f; border-color: #f2cccc; }
  .text { margin: 6px 0 10px; white-space: pre-wrap; word-wrap: break-word; }
  .reason {
    font-size: 13px; color: var(--muted);
    border-left: 3px solid var(--line); padding-left: 10px; margin-bottom: 12px;
  }
  .actions { display: flex; gap: 8px; flex-wrap: wrap; }
  button {
    font-size: 13px; padding: 6px 12px; border-radius: 6px;
    border: 1px solid var(--line); background: #fff; color: var(--ink);
    cursor: pointer;
  }
  button:hover { border-color: #c7ccd4; }
  button.primary { background: var(--ink); color: #fff; border-color: var(--ink); }
  .empty { color: var(--muted); padding: 40px 0; text-align: center; }
  footer { color: var(--muted); font-size: 12px; padding: 0 28px 40px; }
</style>
</head>
<body>

<header>
  <h1>Lead Finder</h1>
  <div class="sub">Posts from people who may be looking to pay for AI, video, or automation work. Built __BUILT__.</div>
  <div class="stats">
    <div class="stat"><b id="statTotal">0</b><span>posts reviewed</span></div>
    <div class="stat"><b id="statStrong">0</b><span>strong leads</span></div>
    <div class="stat"><b id="statMoney">0</b><span>mention budget</span></div>
    <div class="stat"><b id="statShown">0</b><span>showing now</span></div>
  </div>
</header>

<div class="controls">
  <select id="fScore">
    <option value="65">Score 65 and above</option>
    <option value="80">Score 80 and above</option>
    <option value="40">Score 40 and above</option>
    <option value="0">Show everything</option>
  </select>
  <select id="fList"><option value="">All watchlists</option></select>
  <select id="fSort">
    <option value="score">Highest score first</option>
    <option value="date">Newest first</option>
  </select>
  <input type="text" id="fText" placeholder="Search words in posts">
  <label class="inline"><input type="checkbox" id="fMoney"> Budget mentioned only</label>
  <label class="inline"><input type="checkbox" id="fRepeat" checked> Hide repeat authors</label>
  <button id="btnReset">Reset</button>
</div>

<main>
  <div id="feed"></div>
  <div class="empty" id="empty" style="display:none">
    Nothing matches these filters. Loosen them or collect more posts.
  </div>
</main>

<footer>
  Data collected from public posts. Handled manually, no automatic outreach.
</footer>

<script>
var LEADS = __DATA__;
var handled = {};

function band(score) {
  if (score >= 80) return "s-strong";
  if (score >= 65) return "s-good";
  if (score >= 40) return "s-maybe";
  return "s-low";
}

function escapeText(value) {
  var div = document.createElement("div");
  div.textContent = value == null ? "" : String(value);
  return div.innerHTML;
}

function fillWatchlists() {
  var box = document.getElementById("fList");
  var names = [];
  LEADS.forEach(function (lead) {
    if (lead.watchlist && names.indexOf(lead.watchlist) === -1) {
      names.push(lead.watchlist);
    }
  });
  names.forEach(function (name) {
    var option = document.createElement("option");
    option.value = name;
    option.textContent = name;
    box.appendChild(option);
  });
}

function currentFilters() {
  return {
    minScore: parseInt(document.getElementById("fScore").value, 10),
    list: document.getElementById("fList").value,
    sort: document.getElementById("fSort").value,
    words: document.getElementById("fText").value.trim().toLowerCase(),
    moneyOnly: document.getElementById("fMoney").checked,
    hideRepeat: document.getElementById("fRepeat").checked
  };
}

function render() {
  var f = currentFilters();
  var feed = document.getElementById("feed");
  var rows = LEADS.filter(function (lead) {
    if (lead.score < f.minScore) return false;
    if (f.list && lead.watchlist !== f.list) return false;
    if (f.moneyOnly && !lead.budget) return false;
    if (f.hideRepeat && lead.repeat) return false;
    if (f.words && lead.text.toLowerCase().indexOf(f.words) === -1) return false;
    return true;
  });

  if (f.sort === "date") {
    rows.sort(function (a, b) {
      return (b.posted_at || "").localeCompare(a.posted_at || "");
    });
  } else {
    rows.sort(function (a, b) { return b.score - a.score; });
  }

  feed.innerHTML = "";

  rows.forEach(function (lead) {
    var card = document.createElement("div");
    card.className = "card" + (handled[lead.post_id] ? " done" : "");

    var tags = "";
    if (lead.budget) tags += '<span class="tag money">budget mentioned</span>';
    if (lead.repeat) tags += '<span class="tag repeat">repeat author</span>';
    if (lead.watchlist) tags += '<span class="tag">' + escapeText(lead.watchlist) + "</span>";
    if (lead.category) tags += '<span class="tag">' + escapeText(lead.category) + "</span>";

    var profile = lead.author
      ? '<a href="https://x.com/' + escapeText(lead.author) + '" target="_blank">@' + escapeText(lead.author) + "</a>"
      : "unknown author";

    card.innerHTML =
      '<div class="row1">' +
        '<span class="score ' + band(lead.score) + '">' + lead.score + "</span>" +
        '<span class="who">' + profile + "</span>" +
        '<span class="tag">' + escapeText(lead.followers) + " followers</span>" +
        tags +
      "</div>" +
      '<div class="text">' + escapeText(lead.text) + "</div>" +
      '<div class="reason">' + escapeText(lead.reason) + "</div>" +
      '<div class="actions">' +
        '<button class="primary" data-open="' + escapeText(lead.post_url) + '">Open post</button>' +
        '<button data-copy="' + escapeText(lead.post_url) + '">Copy link</button>' +
        '<button data-done="' + escapeText(lead.post_id) + '">' +
          (handled[lead.post_id] ? "Undo" : "Mark handled") +
        "</button>" +
      "</div>";

    feed.appendChild(card);
  });

  document.getElementById("empty").style.display = rows.length ? "none" : "block";
  document.getElementById("statShown").textContent = rows.length;
}

function wireUp() {
  ["fScore", "fList", "fSort", "fMoney", "fRepeat"].forEach(function (id) {
    document.getElementById(id).addEventListener("change", render);
  });
  document.getElementById("fText").addEventListener("input", render);

  document.getElementById("btnReset").addEventListener("click", function () {
    document.getElementById("fScore").value = "65";
    document.getElementById("fList").value = "";
    document.getElementById("fSort").value = "score";
    document.getElementById("fText").value = "";
    document.getElementById("fMoney").checked = false;
    document.getElementById("fRepeat").checked = true;
    render();
  });

  document.getElementById("feed").addEventListener("click", function (event) {
    var button = event.target.closest("button");
    if (!button) return;

    if (button.dataset.open) {
      window.open(button.dataset.open, "_blank");
      return;
    }

    if (button.dataset.copy) {
      var link = button.dataset.copy;
      if (navigator.clipboard) {
        navigator.clipboard.writeText(link);
      }
      button.textContent = "Copied";
      setTimeout(function () { button.textContent = "Copy link"; }, 1200);
      return;
    }

    if (button.dataset.done) {
      var id = button.dataset.done;
      handled[id] = !handled[id];
      render();
    }
  });
}

document.getElementById("statTotal").textContent = LEADS.length;
document.getElementById("statStrong").textContent =
  LEADS.filter(function (l) { return l.score >= 65; }).length;
document.getElementById("statMoney").textContent =
  LEADS.filter(function (l) { return l.budget; }).length;

fillWatchlists();
wireUp();
render();
</script>
</body>
</html>
"""


def main():
    rows = clean(load_scored())

    built = datetime.now().strftime("%d %b %Y at %H:%M")

    # Post text comes from strangers. Neutralise any characters that
    # could close the script tag early or inject markup into the page.
    payload = json.dumps(rows, ensure_ascii=False)
    payload = (payload
               .replace("<", "\\u003c")
               .replace(">", "\\u003e")
               .replace("&", "\\u0026"))

    page = PAGE_TEMPLATE.replace("__DATA__", payload)
    page = page.replace("__BUILT__", html.escape(built))

    with open(OUTPUT_FILE, "w", encoding="utf-8") as handle:
        handle.write(page)

    strong = len([r for r in rows if r["score"] >= GOOD])

    print("=" * 62)
    print("DASHBOARD BUILT")
    print("=" * 62)
    print("  Posts included        :", len(rows))
    print("  Scoring 65 or above   :", strong)
    print("  File created          :", OUTPUT_FILE)
    print("=" * 62)
    print("")
    print("Opening it in your browser now.")

    try:
        webbrowser.open("file://" + os.path.abspath(OUTPUT_FILE))
    except Exception:
        print("Could not open it automatically. "
              "Double click dashboard.html in your folder.")


if __name__ == "__main__":
    main()
