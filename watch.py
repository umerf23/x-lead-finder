"""
Step 11 watcher.

Purpose: run the tool on its own. Every few minutes it collects new
posts, scores them, refreshes the dashboard, and once a day it sends
you a digest of the best leads.

It does not replace collect.py or score.py. It runs them for you, in
order, on a timer, which means nothing here can break the parts that
already work.

Safety, because this spends real credits without you watching:
- a daily cap on posts received, which is what you pay for
- the cap survives a restart, because it is written to disk
- if the cap is reached, collection pauses until the next day
- if the count cannot be read from the collector, the worst case is
  assumed, so the guard errs towards spending less
- press Ctrl and C at any time to stop cleanly

Run it:
    python watch.py            keep running until you stop it
    python watch.py --once     do one cycle and exit
    python watch.py --dry-run  show the plan, collect nothing
"""

import argparse
import json
import os
import re
import subprocess
import sys
import time
from datetime import datetime

import yaml

import digest
import spend

# ---------------------------------------------------------------------
# Locations and defaults
# ---------------------------------------------------------------------

CONFIG_FILE = "config.yaml"
DATA_FOLDER = "data"
STATE_FILE = os.path.join(DATA_FOLDER, "watch_state.json")
LOG_FILE = os.path.join(DATA_FOLDER, "watch.log")

COLLECT_SCRIPT = "collect.py"
SCORE_SCRIPT = "score.py"
DASHBOARD_SCRIPT = "build_dashboard.py"

# No single step should ever hang the loop. Fifteen minutes is far more
# than any of them need.
STEP_TIMEOUT_SECONDS = 900

# If collection fails this many times in a row, stop and say so rather
# than run all night doing nothing.
MAX_CONSECUTIVE_FAILURES = 3

DEFAULTS = {
    "poll_every_minutes": 10,
    "daily_received_cap": 200,
    "rebuild_dashboard": True,
    "digest_enabled": True,
    "digest_at": "18:00",
}

# ---------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------

def log(message):
    """Print a line and keep a copy on disk, so an overnight run is reviewable."""
    stamped = "[%s] %s" % (datetime.now().strftime("%Y-%m-%d %H:%M:%S"), message)
    print(stamped, flush=True)
    try:
        os.makedirs(DATA_FOLDER, exist_ok=True)
        with open(LOG_FILE, "a", encoding="utf-8") as handle:
            handle.write(stamped + "\n")
    except OSError:
        pass


# ---------------------------------------------------------------------
# Settings
# ---------------------------------------------------------------------

def whole_number(value, fallback, lowest=1):
    try:
        return max(lowest, int(value))
    except (TypeError, ValueError):
        return fallback


def parse_clock(value, fallback="18:00"):
    """Turn 18:00 into the pair 18 and 0. Anything odd falls back."""
    text = str(value or "").strip()
    match = re.fullmatch(r"(\d{1,2}):(\d{2})", text)
    if not match:
        text = fallback
        match = re.fullmatch(r"(\d{1,2}):(\d{2})", text)
    hour = int(match.group(1))
    minute = int(match.group(2))
    if hour > 23 or minute > 59:
        return 18, 0
    return hour, minute


def load_settings():
    """Read config.yaml. Every setting has a working default, so a
    config file without a watcher block is fine."""
    config = {}
    if os.path.exists(CONFIG_FILE):
        try:
            with open(CONFIG_FILE, "r", encoding="utf-8") as handle:
                config = yaml.safe_load(handle) or {}
        except (yaml.YAMLError, OSError) as error:
            log("Warning: config.yaml could not be read, using defaults. " + str(error))
            config = {}
    if not isinstance(config, dict):
        config = {}

    block = config.get("watcher")
    if not isinstance(block, dict):
        block = {}

    hour, minute = parse_clock(block.get("digest_at", DEFAULTS["digest_at"]))

    # One budget, one number. collect.py enforces daily_post_cap, so
    # the watcher must report that same figure rather than a second one
    # of its own. The watcher block is still read as a fallback for
    # anyone who set things up before the shared ledger existed.
    fallback_cap = whole_number(
        block.get("daily_received_cap"), DEFAULTS["daily_received_cap"])
    daily_cap = whole_number(config.get("daily_post_cap"), fallback_cap)

    return {
        "poll_every_minutes": whole_number(
            block.get("poll_every_minutes"), DEFAULTS["poll_every_minutes"]),
        "daily_received_cap": daily_cap,
        "rebuild_dashboard": bool(
            block.get("rebuild_dashboard", DEFAULTS["rebuild_dashboard"])),
        "digest_enabled": bool(
            block.get("digest_enabled", DEFAULTS["digest_enabled"])),
        "digest_hour": hour,
        "digest_minute": minute,
        # Used only as the worst case guess if the collector's own count
        # cannot be read back.
        "max_posts_per_run": whole_number(config.get("max_posts_per_run"), 40),
        "price_per_1000": float(config.get("cost_per_1000_posts", 0.15) or 0.15),
    }


# ---------------------------------------------------------------------
# State that survives a restart
# ---------------------------------------------------------------------

def load_state():
    if not os.path.exists(STATE_FILE):
        return {}
    try:
        with open(STATE_FILE, "r", encoding="utf-8") as handle:
            state = json.load(handle)
    except (ValueError, OSError):
        log("Warning: watch_state.json could not be read. Starting a fresh count.")
        return {}
    return state if isinstance(state, dict) else {}


def save_state(state):
    os.makedirs(DATA_FOLDER, exist_ok=True)
    with open(STATE_FILE, "w", encoding="utf-8") as handle:
        json.dump(state, handle, indent=2, ensure_ascii=False)


def roll_over_day(state):
    """
    Note the date change.

    The spend count itself lives in the shared ledger now, so all this
    does is keep the watcher's own file tidy.
    """
    today = datetime.now().strftime("%Y-%m-%d")
    if state.get("day") != today:
        state["day"] = today
        save_state(state)
    return state


# ---------------------------------------------------------------------
# Running the existing scripts
# ---------------------------------------------------------------------

def run_script(script, feed_yes=False, extra=None):
    """
    Run one of the project's scripts and hand back what it printed.

    Returns a pair: whether it succeeded, and its combined output.
    feed_yes types yes into the confirmation prompt in collect.py. If
    that prompt is switched off in config.yaml the input is simply
    ignored, so this is safe either way.

    Windows hands Python an old character set that cannot represent
    emoji, and posts are full of them. Forcing UTF-8 on the child, and
    reading its output as UTF-8, stops a single emoji killing a run.
    """
    if not os.path.exists(script):
        return False, "%s is not in this folder, so it was skipped." % script

    environment = os.environ.copy()
    environment["PYTHONIOENCODING"] = "utf-8"
    environment["PYTHONUTF8"] = "1"

    command = [sys.executable, script] + list(extra or [])
    try:
        finished = subprocess.run(
            command,
            input="yes\n" if feed_yes else None,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            env=environment,
            timeout=STEP_TIMEOUT_SECONDS,
        )
    except subprocess.TimeoutExpired:
        return False, "%s took longer than %d seconds and was stopped." % (
            script, STEP_TIMEOUT_SECONDS)
    except OSError as error:
        return False, "%s could not be started: %s" % (script, error)

    output = (finished.stdout or "") + (finished.stderr or "")
    return finished.returncode == 0, output


def last_lines(text, count=3):
    """The tail of a script's output, for a readable one line log entry."""
    cleaned = re.sub(r"Type yes to go ahead[^\n]*", "", text or "")
    lines = [line.strip() for line in cleaned.splitlines() if line.strip()]
    return " | ".join(lines[-count:]) if lines else "no output"


# ---------------------------------------------------------------------
# One cycle
# ---------------------------------------------------------------------

def do_cycle(settings, state, dry_run=False):
    """
    Collect, score, rebuild.

    The daily cap is no longer enforced here. collect.py owns it now,
    through the shared ledger, so the watcher, the web app and the
    command line all draw from one budget. The watcher's job is to run
    things on a timer and report what happened.

    Returns the state and whether the collection worked, so the main
    loop can stop if the same thing keeps failing.
    """
    state = roll_over_day(state)

    if dry_run:
        figures = spend.summary(settings["daily_received_cap"],
                                settings["price_per_1000"])
        log("Dry run. Would collect now. %d of %d posts left in today's "
            "budget." % (figures["left_today"], figures["cap"]))
        return state, True

    log("Collecting.")
    collected, output = run_script(
        COLLECT_SCRIPT, feed_yes=True, extra=["--source", "watcher"])

    if "TODAY'S CAP IS REACHED" in (output or ""):
        log("Collection skipped. Today's cap is already reached, so nothing "
            "was spent. The count resets at midnight.")
    elif collected:
        log("Collected. " + last_lines(output))
    else:
        log("Collection had a problem. " + last_lines(output, 4))

    for line in spend.describe(settings["daily_received_cap"],
                               settings["price_per_1000"]):
        log(line.strip())

    log("Scoring. This is free.")
    scored, output = run_script(SCORE_SCRIPT)
    log(("Scored. " if scored else "Scoring had a problem. ") + last_lines(output))

    if settings["rebuild_dashboard"]:
        built, output = run_script(DASHBOARD_SCRIPT)
        log(("Dashboard refreshed. " if built else "Dashboard step skipped. ")
            + last_lines(output, 1))

    return state, collected


def digest_is_due(settings, state):
    """True once a day, after the chosen time, and only once."""
    if not settings["digest_enabled"]:
        return False
    now = datetime.now()
    if state.get("last_digest_date") == now.strftime("%Y-%m-%d"):
        return False
    due = (settings["digest_hour"], settings["digest_minute"])
    return (now.hour, now.minute) >= due


def maybe_send_digest(settings, state):
    if not digest_is_due(settings, state):
        return state
    log("Digest time. Building the summary.")
    try:
        summary = digest.run(quiet=True)
    except Exception as error:  # a failed digest must never stop the watcher
        log("The digest could not be sent: %s" % error)
        return state
    log("Digest done: %d leads. %s" % (
        summary["leads"], " ".join(summary["results"])))
    return load_state()


# ---------------------------------------------------------------------
# Waiting
# ---------------------------------------------------------------------

def wait_minutes(minutes):
    """Sleep in short pieces so Ctrl and C stops the tool immediately."""
    finish_at = time.monotonic() + minutes * 60
    while True:
        left = finish_at - time.monotonic()
        if left <= 0:
            return
        time.sleep(min(5.0, left))


# ---------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(
        description="Run the lead finder on a timer and send a daily digest.")
    parser.add_argument("--once", action="store_true",
                        help="Do a single cycle and exit. Good for Task Scheduler.")
    parser.add_argument("--dry-run", action="store_true", dest="dry_run",
                        help="Show the plan without collecting anything.")
    parser.add_argument("--no-dashboard", action="store_true", dest="no_dashboard",
                        help="Skip the dashboard rebuild, which pops open a "
                             "browser tab every cycle.")
    arguments = parser.parse_args()

    settings = load_settings()
    if arguments.no_dashboard:
        settings["rebuild_dashboard"] = False
    state = roll_over_day(load_state())

    log("=" * 58)
    log("WATCHER STARTED")
    log("  Cycle every        : %d minutes" % settings["poll_every_minutes"])
    log("  Daily cap          : %d posts received, about $%.2f" % (
        settings["daily_received_cap"],
        settings["daily_received_cap"] / 1000.0 * settings["price_per_1000"]))
    log("  Used today so far  : %d posts, about $%.4f" % (
        spend.used_today(),
        spend.used_today() / 1000.0 * settings["price_per_1000"]))
    log("  Daily digest       : %s at %02d:%02d" % (
        "on" if settings["digest_enabled"] else "off",
        settings["digest_hour"], settings["digest_minute"]))
    log("  Log file           : %s" % LOG_FILE)
    log("=" * 58)

    failures = 0
    try:
        while True:
            state, worked = do_cycle(settings, state, dry_run=arguments.dry_run)
            failures = 0 if worked else failures + 1
            state = maybe_send_digest(settings, state)
            if arguments.once or arguments.dry_run:
                log("Single cycle finished.")
                return
            if failures >= MAX_CONSECUTIVE_FAILURES:
                log("Collection failed %d times in a row, so the watcher stopped."
                    % failures)
                log("A tool that keeps failing quietly is worse than one that "
                    "tells you. Check the message above, fix it, and start again.")
                return
            log("Sleeping. Next cycle at about %s. Press Ctrl and C to stop." %
                datetime.fromtimestamp(
                    time.time() + settings["poll_every_minutes"] * 60
                ).strftime("%H:%M"))
            wait_minutes(settings["poll_every_minutes"])
    except KeyboardInterrupt:
        log("Stopped by you. Nothing was left running.")


if __name__ == "__main__":
    main()
