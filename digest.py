"""
Step 11 digest.

Purpose: take the leads that score well in data/scored.json, turn them
into a short daily summary, and push that summary to you without you
having to open the tool.

Where it sends, in this order of preference:
- Slack, if SLACK_WEBHOOK_URL is set in .env
- Email, if the SMTP settings are set in .env
- Always, a local copy at data/digest.html and data/digest.txt

Nothing here costs money. It reads a local file and sends a message.

Run it on its own:
    python digest.py --preview     build the files, send nothing
    python digest.py               build and send for real
    python digest.py --all         ignore what was already sent

Or let watch.py call it once a day for you.
"""

import argparse
import html
import json
import os
import smtplib
import ssl
from datetime import datetime
from email.message import EmailMessage

import requests
import yaml
from dotenv import load_dotenv

# ---------------------------------------------------------------------
# Locations and defaults
# ---------------------------------------------------------------------

CONFIG_FILE = "config.yaml"
DATA_FOLDER = "data"
SCORED_FILE = os.path.join(DATA_FOLDER, "scored.json")
STATE_FILE = os.path.join(DATA_FOLDER, "watch_state.json")
DIGEST_HTML = os.path.join(DATA_FOLDER, "digest.html")
DIGEST_TEXT = os.path.join(DATA_FOLDER, "digest.txt")

DEFAULT_MIN_SCORE = 65
DEFAULT_TOP_N = 10

# How many sent post ids to remember. Enough to stop repeats for weeks,
# small enough that the state file stays tiny.
REMEMBERED_IDS = 2000

# The scorer may name its fields slightly differently from one version
# to the next. Rather than break, look for any of these in order.
SCORE_KEYS = ("score", "intent_score", "lead_score", "rating")
REASON_KEYS = ("reason", "why", "explanation", "justification")
CATEGORY_KEYS = ("category", "intent", "type", "watchlist")
BUDGET_KEYS = ("budget_signal", "budget_mentioned", "money_mentioned",
               "has_budget", "budget")

load_dotenv()


# ---------------------------------------------------------------------
# Small helpers
# ---------------------------------------------------------------------

def first_value(record, keys, fallback=None):
    """Return the first key that is present and not empty."""
    for key in keys:
        value = record.get(key)
        if value not in (None, ""):
            return value
    return fallback


def read_score(record):
    """Scores may arrive as 88, 88.0, or "88". Treat them all the same."""
    try:
        return int(float(first_value(record, SCORE_KEYS, 0)))
    except (TypeError, ValueError):
        return 0


def read_budget(record):
    """The budget flag may be a real true or false, or the word yes."""
    value = first_value(record, BUDGET_KEYS, False)
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in ("true", "yes", "y", "1")


def shorten(text, limit=220):
    """Trim long post text so a digest stays readable."""
    clean = " ".join(str(text or "").split())
    if len(clean) <= limit:
        return clean
    return clean[:limit].rstrip() + " ..."


def band(score):
    """Group a score so the reader sees strength before they read words."""
    if score >= 85:
        return "strong", "#0B6E4F"
    if score >= 75:
        return "solid", "#8A5A00"
    return "worth a look", "#55606B"


# ---------------------------------------------------------------------
# Reading files
# ---------------------------------------------------------------------

def load_settings():
    """Read the watcher block from config.yaml, with safe fallbacks."""
    settings = {
        "min_score": DEFAULT_MIN_SCORE,
        "top_n": DEFAULT_TOP_N,
    }
    if not os.path.exists(CONFIG_FILE):
        return settings
    try:
        with open(CONFIG_FILE, "r", encoding="utf-8") as handle:
            config = yaml.safe_load(handle) or {}
    except (yaml.YAMLError, OSError):
        print("Warning: config.yaml could not be read. Using default digest settings.")
        return settings
    block = config.get("watcher")
    if not isinstance(block, dict):
        return settings
    try:
        settings["min_score"] = int(block.get("digest_min_score", DEFAULT_MIN_SCORE))
    except (TypeError, ValueError):
        pass
    try:
        settings["top_n"] = max(1, int(block.get("digest_top_n", DEFAULT_TOP_N)))
    except (TypeError, ValueError):
        pass
    return settings


def load_scored():
    """Load the scored posts. An empty list is a normal result, not a fault."""
    if not os.path.exists(SCORED_FILE):
        return []
    try:
        with open(SCORED_FILE, "r", encoding="utf-8") as handle:
            records = json.load(handle)
    except (ValueError, OSError):
        print("Warning: data/scored.json could not be read.")
        return []
    if isinstance(records, dict):
        # Some versions wrap the list in an object. Find the list inside.
        for value in records.values():
            if isinstance(value, list):
                records = value
                break
    if not isinstance(records, list):
        return []
    return [item for item in records if isinstance(item, dict)]


def load_state():
    if not os.path.exists(STATE_FILE):
        return {}
    try:
        with open(STATE_FILE, "r", encoding="utf-8") as handle:
            state = json.load(handle)
    except (ValueError, OSError):
        return {}
    return state if isinstance(state, dict) else {}


def save_state(state):
    os.makedirs(DATA_FOLDER, exist_ok=True)
    with open(STATE_FILE, "w", encoding="utf-8") as handle:
        json.dump(state, handle, indent=2, ensure_ascii=False)


# ---------------------------------------------------------------------
# Choosing what goes in
# ---------------------------------------------------------------------

def pick_leads(records, min_score, top_n, already_sent, include_all=False):
    """
    Return the best unsent leads, highest score first.

    already_sent is a set of post ids that went out in an earlier digest.
    A digest that repeats yesterday's leads trains you to ignore it.
    """
    chosen = []
    seen_ids = set()
    for record in records:
        post_id = str(record.get("post_id") or "")
        if post_id and post_id in seen_ids:
            continue
        score = read_score(record)
        if score < min_score:
            continue
        if not include_all and post_id and post_id in already_sent:
            continue
        if post_id:
            seen_ids.add(post_id)
        chosen.append({
            "post_id": post_id,
            "score": score,
            "author": record.get("author") or "unknown",
            "author_name": record.get("author_name") or "",
            "text": record.get("text") or "",
            "url": record.get("post_url") or "",
            "posted_at": record.get("posted_at") or "",
            "category": first_value(record, CATEGORY_KEYS, "uncategorised"),
            "reason": first_value(record, REASON_KEYS, ""),
            "evidence": str(record.get("evidence") or "").strip(),
            "budget": read_budget(record),
        })
    chosen.sort(key=lambda lead: lead["score"], reverse=True)
    return chosen[:top_n]


# ---------------------------------------------------------------------
# Writing the digest
# ---------------------------------------------------------------------

def build_text(leads, stamp, held_back=0):
    """A plain text version. This is what Slack receives."""
    lines = ["X LEAD FINDER, DAILY DIGEST", stamp, ""]
    if not leads:
        if held_back:
            lines.append("Nothing new to report.")
            lines.append("%d leads still pass the score threshold, but all of"
                         % held_back)
            lines.append("them went out in an earlier digest, so they are not")
            lines.append("repeated here. They are all still in your dashboard.")
        else:
            lines.append("No leads cleared the score threshold today.")
            lines.append("That is a quiet day, not a broken tool.")
        return "\n".join(lines)

    lines.append("Top %d new leads." % len(leads))
    lines.append("")
    for position, lead in enumerate(leads, start=1):
        label, _ = band(lead["score"])
        lines.append("%d. %d out of 100, %s" % (position, lead["score"], label))
        lines.append("   @%s  %s" % (lead["author"], lead["category"]))
        lines.append("   %s" % shorten(lead["text"]))
        if lead["reason"]:
            lines.append("   Why it matched: %s" % shorten(lead["reason"], 160))
        if lead["evidence"]:
            lines.append("   Their words: %s" % shorten(lead["evidence"], 120))
        if lead["budget"]:
            lines.append("   Budget mentioned in the post.")
        if lead["url"]:
            lines.append("   %s" % lead["url"])
        lines.append("")
    lines.append("Open the dashboard to mark these handled.")
    return "\n".join(lines)


def build_html(leads, stamp, held_back=0):
    """
    A self contained HTML version for email and for keeping on disk.

    Styles are written inline because email clients strip stylesheets.
    """
    safe = html.escape
    parts = [
        "<!doctype html>",
        "<html><head><meta charset='utf-8'>",
        "<meta name='viewport' content='width=device-width, initial-scale=1'>",
        "<title>X Lead Finder digest</title></head>",
        "<body style=\"margin:0;padding:24px;background:#F4F6F8;"
        "font-family:-apple-system,Segoe UI,Roboto,Helvetica,Arial,sans-serif;"
        "color:#11181F;\">",
        "<div style='max-width:640px;margin:0 auto;'>",
        "<p style=\"margin:0 0 4px;font:600 11px/1.4 ui-monospace,Consolas,monospace;"
        "letter-spacing:.14em;text-transform:uppercase;color:#55606B;\">"
        "X Lead Finder</p>",
        "<h1 style='margin:0 0 2px;font-size:24px;font-weight:650;'>Daily digest</h1>",
        "<p style=\"margin:0 0 20px;font:400 13px/1.5 ui-monospace,Consolas,monospace;"
        "color:#55606B;\">%s</p>" % safe(stamp),
    ]

    if not leads:
        if held_back:
            message = (
                "Nothing new to report. %d leads still pass the score "
                "threshold, but all of them went out in an earlier digest, "
                "so they are not repeated here. They are all still waiting "
                "in your dashboard." % held_back
            )
        else:
            message = (
                "No leads cleared the score threshold today. That is a quiet "
                "day, not a broken tool. The watcher is still running."
            )
        parts.append(
            "<div style='background:#FFFFFF;border-radius:10px;padding:20px;'>"
            "<p style='margin:0;font-size:15px;line-height:1.6;'>%s</p></div>"
            % safe(message)
        )
    else:
        parts.append(
            "<p style='margin:0 0 16px;font-size:15px;'>"
            "%d new leads, best first.</p>" % len(leads)
        )
        for lead in leads:
            label, colour = band(lead["score"])
            parts.append(
                "<div style=\"background:#FFFFFF;border-left:4px solid %s;"
                "border-radius:8px;padding:16px 18px;margin:0 0 12px;\">" % colour
            )
            parts.append(
                "<div style=\"font:650 26px/1 ui-monospace,Consolas,monospace;"
                "color:%s;\">%d<span style=\"font-size:11px;font-weight:500;"
                "color:#55606B;\"> / 100 &nbsp;%s</span></div>" % (
                    colour, lead["score"], safe(label))
            )
            parts.append(
                "<div style=\"margin:8px 0 0;font:600 13px/1.4 ui-monospace,"
                "Consolas,monospace;color:#11181F;\">@%s"
                "<span style='font-weight:400;color:#55606B;'> &nbsp;%s</span></div>"
                % (safe(lead["author"]), safe(str(lead["category"])))
            )
            parts.append(
                "<p style='margin:10px 0 0;font-size:15px;line-height:1.55;'>%s</p>"
                % safe(shorten(lead["text"]))
            )
            if lead["reason"]:
                parts.append(
                    "<p style='margin:10px 0 0;font-size:13px;line-height:1.5;"
                    "color:#55606B;'><strong style='color:#11181F;'>Why it matched"
                    "</strong> %s</p>" % safe(shorten(lead["reason"], 200))
                )
            if lead["evidence"]:
                parts.append(
                    "<p style=\"margin:10px 0 0;padding:8px 12px;"
                    "background:#F4F6F8;border-radius:6px;"
                    "font:500 13px/1.5 ui-monospace,Consolas,monospace;"
                    "color:#11181F;\">%s</p>" % safe(shorten(lead["evidence"], 140))
                )
            if lead["budget"]:
                parts.append(
                    "<p style=\"margin:8px 0 0;font:600 11px/1.4 ui-monospace,"
                    "Consolas,monospace;letter-spacing:.08em;text-transform:uppercase;"
                    "color:#0B6E4F;\">Budget mentioned</p>"
                )
            if lead["url"]:
                parts.append(
                    "<p style='margin:12px 0 0;'><a href='%s' "
                    "style=\"font-size:14px;font-weight:600;color:#11181F;\">"
                    "Open the post on X</a></p>" % safe(lead["url"])
                )
            parts.append("</div>")

    parts.append(
        "<p style=\"margin:20px 0 0;font:400 12px/1.5 ui-monospace,Consolas,monospace;"
        "color:#7A848E;\">Sent by your own copy of X Lead Finder. "
        "Nothing was contacted automatically.</p>"
    )
    parts.append("</div></body></html>")
    return "\n".join(parts)


def write_files(text_body, html_body):
    os.makedirs(DATA_FOLDER, exist_ok=True)
    with open(DIGEST_TEXT, "w", encoding="utf-8") as handle:
        handle.write(text_body)
    with open(DIGEST_HTML, "w", encoding="utf-8") as handle:
        handle.write(html_body)


# ---------------------------------------------------------------------
# Sending
# ---------------------------------------------------------------------

def send_slack(text_body):
    """Post to a Slack incoming webhook. Returns a plain English result."""
    webhook = os.getenv("SLACK_WEBHOOK_URL", "").strip()
    if not webhook:
        return None
    try:
        response = requests.post(webhook, json={"text": text_body}, timeout=20)
    except requests.exceptions.RequestException as error:
        return ("Slack could not be reached, so nothing was posted there. "
                "Check SLACK_WEBHOOK_URL in .env. Technical detail: %s"
                % type(error).__name__)
    if response.status_code == 200:
        return "Slack, sent."
    return "Slack refused the message. Status %d, %s" % (
        response.status_code, response.text[:120])


def send_email(subject, text_body, html_body):
    """Send through any normal SMTP mailbox. Returns a plain English result."""
    host = os.getenv("SMTP_HOST", "").strip()
    user = os.getenv("SMTP_USER", "").strip()
    password = os.getenv("SMTP_PASS", "").strip()
    recipient = os.getenv("DIGEST_TO", "").strip() or user
    if not (host and user and password and recipient):
        return None

    try:
        port = int(os.getenv("SMTP_PORT", "465"))
    except ValueError:
        port = 465

    message = EmailMessage()
    message["Subject"] = subject
    message["From"] = user
    message["To"] = recipient
    message.set_content(text_body)
    message.add_alternative(html_body, subtype="html")

    context = ssl.create_default_context()
    try:
        if port == 587:
            with smtplib.SMTP(host, port, timeout=30) as server:
                server.starttls(context=context)
                server.login(user, password)
                server.send_message(message)
        else:
            with smtplib.SMTP_SSL(host, port, context=context, timeout=30) as server:
                server.login(user, password)
                server.send_message(message)
    except (smtplib.SMTPException, OSError) as error:
        return "Email failed: %s" % error
    return "Email, sent to %s." % recipient


# ---------------------------------------------------------------------
# The one function watch.py calls
# ---------------------------------------------------------------------

def run(preview=False, include_all=False, quiet=False):
    """
    Build the digest and, unless preview is on, send it.

    Returns a small summary so the caller can log it.
    """
    def say(line):
        if not quiet:
            print(line)

    settings = load_settings()
    state = load_state()
    already_sent = set(state.get("digested_ids") or [])

    records = load_scored()
    leads = pick_leads(records, settings["min_score"], settings["top_n"],
                       already_sent, include_all=include_all)

    # If the digest is empty, work out whether that is because nothing
    # scored well or because everything good has already been sent. The
    # two situations need very different wording.
    held_back = 0
    if not leads:
        held_back = len(pick_leads(records, settings["min_score"],
                                   settings["top_n"], set(), include_all=True))

    now = datetime.now()
    stamp = now.strftime("%d %B %Y, %H:%M")
    text_body = build_text(leads, stamp, held_back)
    html_body = build_html(leads, stamp, held_back)
    write_files(text_body, html_body)

    subject = "X Lead Finder, %d new leads, %s" % (len(leads), now.strftime("%d %b"))
    results = []

    if preview:
        results.append("Preview only, nothing was sent.")
    else:
        slack_result = send_slack(text_body)
        if slack_result:
            results.append(slack_result)
        email_result = send_email(subject, text_body, html_body)
        if email_result:
            results.append(email_result)
        if not results:
            results.append("No Slack or email set up, so the digest was saved locally only.")

        if leads:
            fresh = [lead["post_id"] for lead in leads if lead["post_id"]]
            remembered = list(already_sent) + fresh
            state["digested_ids"] = remembered[-REMEMBERED_IDS:]
        state["last_digest_date"] = now.strftime("%Y-%m-%d")
        save_state(state)

    say("Digest built: %d leads scoring %d or above."
        % (len(leads), settings["min_score"]))
    if held_back:
        say("  %d leads were held back because they were sent before." % held_back)
        say("  To see them again, run: python check_data.py --forget-sent")
    for line in results:
        say("  " + line)
    say("  Saved to %s and %s" % (DIGEST_HTML, DIGEST_TEXT))

    return {"leads": len(leads), "results": results, "preview": preview}


def main():
    parser = argparse.ArgumentParser(
        description="Build and send the daily lead digest.")
    parser.add_argument("--preview", action="store_true",
                        help="Build the files but send nothing.")
    parser.add_argument("--all", action="store_true", dest="include_all",
                        help="Include leads that were already sent before.")
    arguments = parser.parse_args()
    run(preview=arguments.preview, include_all=arguments.include_all)


if __name__ == "__main__":
    main()
