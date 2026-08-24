"""
The spend ledger.

Purpose: one honest record of how many posts have been paid for today,
shared by every route that can spend money.

Before this existed there were three ways to spend and no shared
memory between them:

  python collect.py          from PowerShell, counted nothing
  the Collect now button     in the web app, counted nothing
  python watch.py            counted, but only its own runs

So the watcher could believe you had used 40 of your 200 daily posts
while you had actually bought 300 through the browser. A guard that
measures one third of your spending is not a guard.

Every route runs collect.py, so collect.py is where the counting
belongs. This module is the shared notebook it writes in, and anything
else that wants to report on spending reads the same file.

The ledger lives at data/spend.json and looks like this:

    {
      "day": "2026-08-24",
      "received_today": 60,
      "runs_today": [
        {"at": "2026-08-24T09:12:03", "source": "watcher", "received": 20}
      ],
      "months": {"2026-08": 480}
    }

Nothing here talks to the internet or costs anything.
"""

import json
import os
from datetime import datetime

DATA_FOLDER = "data"
LEDGER_FILE = os.path.join(DATA_FOLDER, "spend.json")

# Keep the day's runs readable rather than endless.
MAX_RUNS_REMEMBERED = 50

DEFAULT_DAILY_CAP = 200


def today_key():
    return datetime.now().strftime("%Y-%m-%d")


def month_key():
    return datetime.now().strftime("%Y-%m")


def whole_number(value, fallback, lowest=0):
    try:
        return max(lowest, int(value))
    except (TypeError, ValueError):
        return fallback


def load():
    """
    Read the ledger, rolling it over if the date has changed.

    A missing or damaged file is treated as a fresh day. That errs
    towards letting you spend rather than blocking you, which is the
    right way round for a file that is only advisory, but it is worth
    knowing that deleting it resets today's count.
    """
    blank = {"day": today_key(), "received_today": 0,
             "runs_today": [], "months": {}}

    if not os.path.exists(LEDGER_FILE):
        return blank

    try:
        with open(LEDGER_FILE, "r", encoding="utf-8") as handle:
            ledger = json.load(handle)
    except (ValueError, OSError):
        return blank

    if not isinstance(ledger, dict):
        return blank

    ledger.setdefault("months", {})
    ledger.setdefault("runs_today", [])
    ledger["received_today"] = whole_number(ledger.get("received_today"), 0)

    if ledger.get("day") != today_key():
        ledger["day"] = today_key()
        ledger["received_today"] = 0
        ledger["runs_today"] = []

    if not isinstance(ledger["months"], dict):
        ledger["months"] = {}
    if not isinstance(ledger["runs_today"], list):
        ledger["runs_today"] = []

    return ledger


def save(ledger):
    """Write the ledger safely, so a crash mid write cannot corrupt it."""
    os.makedirs(DATA_FOLDER, exist_ok=True)
    temporary = LEDGER_FILE + ".tmp"
    with open(temporary, "w", encoding="utf-8") as handle:
        json.dump(ledger, handle, indent=2, ensure_ascii=False)
    os.replace(temporary, LEDGER_FILE)


def used_today():
    return load()["received_today"]


def remaining(cap):
    """How many posts may still be bought today. Never below zero."""
    return max(0, whole_number(cap, DEFAULT_DAILY_CAP) - used_today())


def record(received, source="command line"):
    """
    Add a completed run to the ledger and return the updated record.

    Called once per collection, with the number of posts the supplier
    actually sent, which is what you pay for.
    """
    received = whole_number(received, 0)
    ledger = load()

    ledger["received_today"] += received

    month = month_key()
    ledger["months"][month] = whole_number(
        ledger["months"].get(month), 0) + received

    ledger["runs_today"].append({
        "at": datetime.now().isoformat(timespec="seconds"),
        "source": str(source)[:40],
        "received": received,
    })
    ledger["runs_today"] = ledger["runs_today"][-MAX_RUNS_REMEMBERED:]

    save(ledger)
    return ledger


def summary(cap, price_per_1000):
    """
    A plain reckoning of today and this month, for printing or for the
    web app to display.
    """
    ledger = load()
    cap = whole_number(cap, DEFAULT_DAILY_CAP)
    try:
        price = float(price_per_1000)
    except (TypeError, ValueError):
        price = 0.15

    today = ledger["received_today"]
    month = whole_number(ledger["months"].get(month_key()), 0)

    return {
        "day": ledger["day"],
        "cap": cap,
        "used_today": today,
        "left_today": max(0, cap - today),
        "cost_today": round(today / 1000.0 * price, 4),
        "used_this_month": month,
        "cost_this_month": round(month / 1000.0 * price, 4),
        "runs_today": len(ledger["runs_today"]),
        "price_per_1000": price,
    }


def describe(cap, price_per_1000):
    """The same reckoning as a few lines of readable text."""
    figures = summary(cap, price_per_1000)
    lines = [
        "  Paid for today      : %d of %d posts, about $%.4f" % (
            figures["used_today"], figures["cap"], figures["cost_today"]),
        "  Left today          : %d posts" % figures["left_today"],
        "  This month so far   : %d posts, about $%.4f" % (
            figures["used_this_month"], figures["cost_this_month"]),
    ]
    # The supplier sells whole pages of about twenty posts, so the last
    # page of a run can carry the day past its cap. Saying so is better
    # than a reader wondering why the number looks wrong.
    if figures["used_today"] > figures["cap"]:
        lines.append(
            "  Note                : the last page went %d over the cap, "
            "because" % (figures["used_today"] - figures["cap"]))
        lines.append(
            "                        posts are sold in whole pages of "
            "about twenty.")
    return lines


if __name__ == "__main__":
    # Running this file on its own just reports. It changes nothing.
    print("=" * 62)
    print("SPEND SO FAR")
    print("=" * 62)
    for line in describe(DEFAULT_DAILY_CAP, 0.15):
        print(line)
    print("=" * 62)
    print("Cap shown is the default. collect.py uses the one in")
    print("config.yaml, under daily_post_cap.")
