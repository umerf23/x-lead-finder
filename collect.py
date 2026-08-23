"""
Step 8 collector.

Purpose: read config.yaml, pull matching posts from X, and save them
to a local file so that every later step can work offline without
spending more credits.

Protections built in:
  - a hard cap on posts received per run, which is what you pay for
  - a cap on how many posts any single author may contribute
  - a cap on how many pages a watchlist may request
  - a cost estimate shown before anything is spent
  - a yes or no confirmation prompt
  - duplicate posts are never saved twice

What changed in Step 8:
  - The spend cap now counts posts received from the supplier, not
    posts kept. You pay for what arrives, so that is what is capped.
  - Duplicates no longer eat the cap. The collector keeps looking
    until it has filled the watchlist or run out of pages.
  - A per author cap stops one loud account filling your results.
    It counts posts already in storage, so it holds across runs.
  - Each watchlist now reports received, saved, duplicate, and
    author capped counts, so an empty result is never a mystery.

Run it with:

    python collect.py
"""

import json
import os
from collections import Counter
from datetime import datetime, timezone

import requests
import yaml
from dotenv import load_dotenv

# ---------------------------------------------------------------------
# Setup
# ---------------------------------------------------------------------

CONFIG_FILE = "config.yaml"
DATA_FOLDER = "data"
POSTS_FILE = os.path.join(DATA_FOLDER, "posts.json")

SEARCH_URL = "https://api.twitterapi.io/twitter/tweet/advanced_search"

load_dotenv()
API_KEY = os.getenv("TWITTER_API_KEY")


def stop(message):
    """Print a plain English message and end the program."""
    print(message)
    raise SystemExit(1)


def whole_number(value, fallback, lowest=0):
    """Read a setting as a whole number, falling back if it is unusable."""
    try:
        number = int(value)
    except (TypeError, ValueError):
        return fallback
    return max(lowest, number)


# ---------------------------------------------------------------------
# Load the settings file
# ---------------------------------------------------------------------

def load_config():
    if not os.path.exists(CONFIG_FILE):
        stop("Could not find config.yaml in this folder. "
             "Make sure it sits next to collect.py.")

    try:
        with open(CONFIG_FILE, "r", encoding="utf-8") as handle:
            config = yaml.safe_load(handle)
    except yaml.YAMLError as error:
        stop("config.yaml could not be read. The spacing is probably off.\n"
             "Details: " + str(error))

    if not isinstance(config, dict):
        stop("config.yaml is empty or malformed.")

    watchlists = config.get("watchlists") or []
    if not watchlists:
        stop("No watchlists found in config.yaml.")

    active = [w for w in watchlists if isinstance(w, dict) and w.get("enabled")]
    if not active:
        stop("Every watchlist in config.yaml is switched off. "
             "Set enabled: true on at least one.")

    config["watchlists"] = active
    return config


# ---------------------------------------------------------------------
# Load and save the local store of posts
# ---------------------------------------------------------------------

def load_saved_posts():
    if not os.path.exists(POSTS_FILE):
        return []

    try:
        with open(POSTS_FILE, "r", encoding="utf-8") as handle:
            posts = json.load(handle)
    except (ValueError, OSError):
        print("Warning: existing posts file could not be read. Starting fresh.")
        return []

    if not isinstance(posts, list):
        return []

    return [post for post in posts if isinstance(post, dict)]


def save_posts(posts):
    os.makedirs(DATA_FOLDER, exist_ok=True)
    with open(POSTS_FILE, "w", encoding="utf-8") as handle:
        json.dump(posts, handle, indent=2, ensure_ascii=False)


# ---------------------------------------------------------------------
# Talk to the supplier
# ---------------------------------------------------------------------

def fetch_page(query, cursor):
    """
    Ask for one page of results.

    Returns a pair. The first item is the list of tweets, or None if
    the request failed and this watchlist should be abandoned. The
    second item is the cursor for the next page, or an empty string
    when there are no more pages.
    """
    params = {"query": query, "queryType": "Latest"}
    if cursor:
        params["cursor"] = cursor

    headers = {"X-API-Key": API_KEY}

    try:
        response = requests.get(SEARCH_URL, headers=headers,
                                params=params, timeout=30)
    except requests.exceptions.RequestException as error:
        print("   Could not reach the server. Skipping this watchlist.")
        print("   Details:", error)
        return None, ""

    if response.status_code in (401, 403):
        stop("Your API key was rejected. Open .env and check it is correct.")

    if response.status_code == 402:
        stop("You appear to be out of credits. "
             "Top up or wait before running again.")

    if response.status_code == 429:
        print("   Too many requests too quickly. Skipping this watchlist.")
        return None, ""

    if response.status_code != 200:
        print("   Server refused the request. Status:", response.status_code)
        print("   Message:", response.text[:300])
        return None, ""

    try:
        data = response.json()
    except ValueError:
        print("   The server replied in an unexpected format. Skipping.")
        return None, ""

    tweets = data.get("tweets")
    if not isinstance(tweets, list):
        tweets = []

    next_cursor = data.get("next_cursor") or ""
    if not data.get("has_next_page", False):
        next_cursor = ""

    return tweets, next_cursor


def tidy(tweet, watchlist_name):
    """Keep only the fields we actually need, in a predictable shape."""
    author = tweet.get("author") or {}

    return {
        "post_id": str(tweet.get("id", "")),
        "author": author.get("userName", ""),
        "author_name": author.get("name", ""),
        "followers": author.get("followers", 0),
        "text": tweet.get("text", ""),
        "post_url": tweet.get("url", ""),
        "posted_at": tweet.get("createdAt", ""),
        "likes": tweet.get("likeCount", 0),
        "replies": tweet.get("replyCount", 0),
        "watchlist": watchlist_name,
        "fetched_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }


# ---------------------------------------------------------------------
# Collecting one watchlist
# ---------------------------------------------------------------------

def collect_watchlist(watchlist, limits, state):
    """
    Fill one watchlist up to its cap.

    limits holds the caps for this run. state holds everything shared
    across watchlists: the saved posts, the ids already seen, the count
    of posts per author, and how many posts have been received so far.

    Returns a small report so the caller can print it.
    """
    name = watchlist.get("name", "unnamed")
    query = (watchlist.get("query") or "").strip()

    report = {
        "name": name,
        "received": 0,
        "saved": 0,
        "duplicates": 0,
        "author_capped": 0,
        "pages": 0,
        "note": "",
    }

    if not query:
        report["note"] = "No query set for this watchlist, so it was skipped."
        return report

    cursor = ""

    while (report["saved"] < limits["per_watchlist"]
           and report["pages"] < limits["pages_per_watchlist"]
           and state["received"] < limits["per_run"]):

        tweets, cursor = fetch_page(query, cursor)
        report["pages"] += 1

        if tweets is None:
            report["note"] = "The request failed, so this watchlist stopped early."
            break

        report["received"] += len(tweets)
        state["received"] += len(tweets)

        for tweet in tweets:
            if report["saved"] >= limits["per_watchlist"]:
                break

            record = tidy(tweet, name)
            post_id = record["post_id"]

            if not post_id:
                continue

            if post_id in state["seen_ids"]:
                report["duplicates"] += 1
                continue

            author = (record["author"] or "").lower()
            if author and state["per_author"][author] >= limits["per_author"]:
                report["author_capped"] += 1
                continue

            state["seen_ids"].add(post_id)
            state["per_author"][author] += 1
            state["saved"].append(record)
            report["saved"] += 1

        if not cursor:
            break

    if not report["note"]:
        report["note"] = explain(report, limits)

    return report


def explain(report, limits):
    """Turn the counts into one plain sentence a non-developer can act on."""
    if report["received"] == 0:
        return ("Nothing matched. The query is probably too narrow, "
                "or nothing recent fits it. Try loosening it in config.yaml.")

    if report["saved"] >= limits["per_watchlist"]:
        return "Filled its quota."

    if report["duplicates"] > report["saved"]:
        return ("Mostly posts you already had. Widen the query or wait "
                "for new activity.")

    if report["author_capped"] > 0:
        return ("Some posts were held back by the per author cap, "
                "which is working as intended. Raise "
                "max_posts_per_author in config.yaml to loosen it.")

    return "The supplier ran out of matching results before the cap."


# ---------------------------------------------------------------------
# Main routine
# ---------------------------------------------------------------------

def main():
    if not API_KEY:
        stop("No API key found. Check that .env exists in this folder "
             "and contains TWITTER_API_KEY=your_key")

    config = load_config()
    watchlists = config["watchlists"]

    limits = {
        "per_run": whole_number(config.get("max_posts_per_run"), 40, lowest=1),
        "per_watchlist": whole_number(
            config.get("max_posts_per_watchlist"), 20, lowest=1),
        "per_author": whole_number(
            config.get("max_posts_per_author"), 2, lowest=1),
        "pages_per_watchlist": whole_number(
            config.get("max_pages_per_watchlist"), 3, lowest=1),
    }

    price_per_1000 = float(config.get("cost_per_1000_posts", 0.15))
    confirm = bool(config.get("confirm_before_spending", True))

    worst_case = limits["per_run"]
    estimate = worst_case / 1000.0 * price_per_1000

    print("=" * 62)
    print("COLLECTION PLAN")
    print("=" * 62)
    for watchlist in watchlists:
        print("  Watchlist :", watchlist.get("name", "unnamed"))
    print("  Active watchlists     :", len(watchlists))
    print("  Most posts received   :", worst_case)
    print("  Kept per watchlist    :", limits["per_watchlist"])
    print("  Kept per author       :", limits["per_author"])
    print("  Pages per watchlist   :", limits["pages_per_watchlist"])
    print("  Worst case cost       : about $%.4f" % estimate)
    print("=" * 62)
    print("You pay for posts received, not posts kept, so the figure")
    print("above is the ceiling. The real spend is usually lower.")
    print("")

    if confirm:
        answer = input("Type yes to go ahead, anything else to cancel: ")
        if answer.strip().lower() != "yes":
            print("Cancelled. Nothing was spent.")
            return

    saved = load_saved_posts()

    state = {
        "saved": saved,
        "seen_ids": {post.get("post_id") for post in saved},
        # Counting what is already in storage, not only what arrives
        # this run. Otherwise a loud account wins two more slots every
        # time you collect, which defeats the point of the cap.
        "per_author": Counter(
            (post.get("author") or "").lower() for post in saved),
        "received": 0,
    }

    reports = []

    for watchlist in watchlists:
        print("")
        print("Searching:", watchlist.get("name", "unnamed"))

        if state["received"] >= limits["per_run"]:
            print("   Stopped. The run wide cap was already reached.")
            reports.append({
                "name": watchlist.get("name", "unnamed"),
                "received": 0, "saved": 0, "duplicates": 0,
                "author_capped": 0, "pages": 0,
                "note": "Skipped, the run wide cap was already reached.",
            })
            continue

        report = collect_watchlist(watchlist, limits, state)
        reports.append(report)

        print("   Received from supplier :", report["received"])
        print("   New posts saved        :", report["saved"])
        print("   Already had            :", report["duplicates"])
        print("   Held back, author cap  :", report["author_capped"])
        print("   Pages requested        :", report["pages"])
        print("   ", report["note"])

    save_posts(state["saved"])

    total_received = state["received"]
    total_saved = sum(r["saved"] for r in reports)
    actual_cost = total_received / 1000.0 * price_per_1000

    print("")
    print("=" * 62)
    print("RUN COMPLETE")
    print("=" * 62)
    print("  Posts received, paid for :", total_received)
    print("  New posts saved          :", total_saved)
    print("  Already had              :", sum(r["duplicates"] for r in reports))
    print("  Held back, author cap    :", sum(r["author_capped"] for r in reports))
    print("  Approximate spend        : about $%.4f" % actual_cost)
    print("  Total posts in storage   :", len(state["saved"]))
    print("  Saved to                 :", POSTS_FILE)
    print("=" * 62)

    empty = [r["name"] for r in reports if r["received"] == 0 and r["pages"] > 0]
    if empty:
        print("")
        print("These watchlists matched nothing this time:")
        for name in empty:
            print("   -", name)
        print("That is a query problem, not a fault. Open config.yaml and")
        print("loosen the query, for example by removing one required phrase.")

    print("")
    print("All later steps read from that file, so they cost nothing.")


if __name__ == "__main__":
    main()
