"""
Step 8b collector.

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

What changed in Step 8b, and why it matters most of all:
- The collector now remembers when it last checked each watchlist
  and asks the supplier only for posts newer than that, using the
  since_time operator that X advanced search already understands.
- Before this change, every run bought the same posts again and
  threw them away as duplicates. On a ten minute loop that empties
  a daily budget in under two hours and finds nothing new.
- If you edit a watchlist query, its memory is cleared automatically,
  because a new question deserves a full answer rather than only the
  last ten minutes.
- Receiving nothing is now the normal, healthy result on a short
  cycle, and the wording says so instead of blaming your query.

Run it with:
    python collect.py            normal, only posts since the last check
    python collect.py --full     ignore the memory, search from scratch
"""

import argparse
import json
import os
import time
from collections import Counter
from datetime import datetime, timezone

import yaml
from dotenv import load_dotenv

import sources
import spend

# ---------------------------------------------------------------------
# Setup
# ---------------------------------------------------------------------

CONFIG_FILE = "config.yaml"
DATA_FOLDER = "data"
POSTS_FILE = os.path.join(DATA_FOLDER, "posts.json")
STATE_FILE = os.path.join(DATA_FOLDER, "collect_state.json")

# How far back to reach beyond the last check. Posts do not always
# appear in search the instant they are written, so a small overlap
# stops a slow post falling through the gap between two runs. The
# duplicate filter cleans up anything this catches twice.
DEFAULT_OVERLAP_MINUTES = 5

# The supplier limits how fast you may ask. A short pause before every
# request keeps you under that limit, and costs nothing but seconds.
DEFAULT_REQUEST_DELAY = 1.5

# If we are refused anyway, wait these many seconds and try again,
# rather than abandoning the watchlist and losing the window.
RETRY_WAITS = (5, 15, 30)

load_dotenv()


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
# Remember when each watchlist was last checked
# ---------------------------------------------------------------------

def load_collect_state():
    """
    Return the memory of previous runs, shaped like this:

        {"watchlists": {"AI UGC video": {"checked_at": 1756000000,
                                         "query": "..."}}}

    A missing or damaged file is not a fault. It simply means the next
    run searches from scratch, which is safe, only slightly costlier.
    """
    if not os.path.exists(STATE_FILE):
        return {"watchlists": {}}
    try:
        with open(STATE_FILE, "r", encoding="utf-8") as handle:
            state = json.load(handle)
    except (ValueError, OSError):
        print("Warning: collect_state.json could not be read. "
              "This run will search from scratch.")
        return {"watchlists": {}}
    if not isinstance(state, dict) or not isinstance(state.get("watchlists"), dict):
        return {"watchlists": {}}
    return state


def save_collect_state(state):
    os.makedirs(DATA_FOLDER, exist_ok=True)
    with open(STATE_FILE, "w", encoding="utf-8") as handle:
        json.dump(state, handle, indent=2, ensure_ascii=False)


def since_time_for(watchlist, memory, overlap_minutes, ignore_memory):
    """
    Work out the earliest moment this watchlist should look back to.

    Returns a unix timestamp in seconds, or None to search from
    scratch. Searching from scratch is right in three cases: the first
    ever run, a run forced with --full, and a run where the query has
    been edited, because a new question deserves the full history
    rather than only the last few minutes.
    """
    if ignore_memory:
        return None

    name = watchlist.get("name", "unnamed")
    remembered = memory.get("watchlists", {}).get(name)
    if not isinstance(remembered, dict):
        return None

    if (remembered.get("query") or "") != (watchlist.get("query") or "").strip():
        print("  Query has changed since the last run, so this watchlist "
              "is searched in full.")
        return None

    try:
        checked_at = int(remembered.get("checked_at"))
    except (TypeError, ValueError):
        return None
    if checked_at <= 0:
        return None

    return max(0, checked_at - overlap_minutes * 60)


# ---------------------------------------------------------------------
# Talk to the supplier
# ---------------------------------------------------------------------

def fetch_page(source, query, cursor, since_time, limits):
    """
    Ask the chosen source for one page.

    Every difference between suppliers lives in sources.py, so this
    function neither knows nor cares which one is in use.
    """
    return source.fetch_page(query, cursor, since_time,
                             limits["delay"], limits["retries"])


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

def collect_watchlist(watchlist, limits, state, source, query, since_time):
    """
    Fill one watchlist up to its cap.

    limits holds the caps for this run. state holds everything shared
    across watchlists: the saved posts, the ids already seen, the count
    of posts per author, and how many posts have been received so far.
    since_time is handed to the source rather than written into the
    query, because the two suppliers take it in different ways.

    Returns a small report so the caller can print it.
    """
    name = watchlist.get("name", "unnamed")
    incremental = since_time is not None

    report = {
        "name": name,
        "received": 0,
        "saved": 0,
        "duplicates": 0,
        "author_capped": 0,
        "pages": 0,
        "incremental": incremental,
        "reached_supplier": False,
        "note": "",
    }

    if not (watchlist.get("query") or "").strip():
        report["note"] = "No query set for this watchlist, so it was skipped."
        return report

    cursor = ""

    while (report["saved"] < limits["per_watchlist"]
           and report["pages"] < limits["pages_per_watchlist"]
           and state["received"] < limits["per_run"]):

        tweets, cursor = fetch_page(source, query, cursor,
                                    since_time, limits)
        report["pages"] += 1

        if tweets is None:
            report["note"] = ("The supplier stopped answering, so this "
                              "watchlist ended early. See the message above.")
            break

        # A clean answer means we have seen everything up to now. Only
        # then is it safe to move the watermark forward.
        report["reached_supplier"] = True

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
        if report["incremental"]:
            return ("No new posts since the last check. On a short cycle "
                    "that is the normal, healthy result.")
        return ("Nothing matched. The query is probably too narrow, "
                "or nothing recent fits it. Try loosening it in config.yaml.")
    if report["saved"] >= limits["per_watchlist"]:
        return "Filled its quota."
    if report["duplicates"] > report["saved"]:
        if report["incremental"]:
            return ("Mostly posts you already had, caught again by the "
                    "overlap window. Harmless, and not saved twice.")
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
    parser = argparse.ArgumentParser(
        description="Collect matching posts from X into a local file.")
    parser.add_argument("--full", action="store_true",
                        help="Ignore the memory of previous runs and search "
                             "from scratch. Costs more, so use it sparingly.")
    parser.add_argument("--override", action="store_true",
                        help="Collect even though today's cap is reached. "
                             "Only available from the command line, on "
                             "purpose.")
    parser.add_argument("--source", default="command line",
                        help="Who asked for this run. Recorded in the "
                             "spend ledger.")
    arguments = parser.parse_args()

    config = load_config()
    watchlists = config["watchlists"]

    # Which supplier, and therefore which key and which price. Every
    # difference between them lives in sources.py.
    chosen_source = config.get("data_source", sources.DEFAULT_SOURCE)
    try:
        source = sources.build(
            chosen_source, os.getenv(sources.key_name_for(chosen_source)))
    except sources.SourceError as error:
        stop(str(error))

    limits = {
        "per_run": whole_number(config.get("max_posts_per_run"), 40, lowest=1),
        "per_watchlist": whole_number(
            config.get("max_posts_per_watchlist"), 20, lowest=1),
        "per_author": whole_number(
            config.get("max_posts_per_author"), 2, lowest=1),
        "pages_per_watchlist": whole_number(
            config.get("max_pages_per_watchlist"), 3, lowest=1),
        "retries": whole_number(
            config.get("rate_limit_retries"), len(RETRY_WAITS), lowest=0),
    }
    try:
        limits["delay"] = max(0.0, float(
            config.get("request_delay_seconds", DEFAULT_REQUEST_DELAY)))
    except (TypeError, ValueError):
        limits["delay"] = DEFAULT_REQUEST_DELAY
    try:
        price_per_1000 = float(config.get(
            "cost_per_1000_posts", source.default_price_per_1000))
    except (TypeError, ValueError):
        price_per_1000 = source.default_price_per_1000

    # Switching source without changing the price gives a cost estimate
    # that is wrong by a factor of thirty, which is worse than no
    # estimate at all. Say so rather than quietly reporting nonsense.
    expected = source.default_price_per_1000
    price_warning = ""
    if expected > 0 and not (expected / 3.0 <= price_per_1000 <= expected * 3.0):
        price_warning = (
            "  Warning             : cost_per_1000_posts is set to $%.2f, but "
            "%s\n                        normally charges about $%.2f. Every "
            "figure below\n                        is based on your number, "
            "so it may be badly wrong."
            % (price_per_1000, source.label, expected))
    confirm = bool(config.get("confirm_before_spending", True))
    only_new = bool(config.get("only_new_posts", True)) and not arguments.full
    overlap = whole_number(config.get("since_overlap_minutes"),
                           DEFAULT_OVERLAP_MINUTES, lowest=0)

    worst_case = limits["per_run"]

    # The daily cap belongs to the whole tool, not to one route into it.
    # Look for it at the top level first, then in the watcher block for
    # anyone who set it up before the ledger existed.
    watcher_block = config.get("watcher")
    fallback_cap = spend.DEFAULT_DAILY_CAP
    if isinstance(watcher_block, dict):
        fallback_cap = whole_number(
            watcher_block.get("daily_received_cap"), spend.DEFAULT_DAILY_CAP)
    daily_cap = whole_number(config.get("daily_post_cap"), fallback_cap, lowest=1)

    left_today = spend.remaining(daily_cap)

    if left_today <= 0 and not arguments.override:
        print("=" * 62)
        print("STOPPED, TODAY'S CAP IS REACHED")
        print("=" * 62)
        for line in spend.describe(daily_cap, price_per_1000):
            print(line)
        print("=" * 62)
        print("Nothing was collected and nothing was spent.")
        print("")
        print("The count resets at midnight. If you need more today,")
        print("either raise daily_post_cap in config.yaml, or run:")
        print("")
        print("    python collect.py --override")
        print("")
        print("Override works only from PowerShell. The Collect now")
        print("button and the watcher cannot use it, because those are")
        print("the two times you are least likely to be watching.")
        return

    if arguments.override and left_today <= 0:
        print("Override in use. Today's cap is already reached and you")
        print("are choosing to spend beyond it.")
        print("")
    elif left_today < worst_case:
        # Never let one run leap over the cap. Ask for less instead.
        limits["per_run"] = left_today
        worst_case = left_today

    estimate = worst_case / 1000.0 * price_per_1000

    memory = load_collect_state()
    run_started = int(time.time())

    print("=" * 62)
    print("COLLECTION PLAN")
    print("=" * 62)
    for watchlist in watchlists:
        print("  Watchlist           :", watchlist.get("name", "unnamed"))
    print("  Active watchlists   :", len(watchlists))
    print("  Data source         : %s (%s)" % (source.name, source.label))
    print("  Most posts received :", worst_case)
    print("  Kept per watchlist  :", limits["per_watchlist"])
    print("  Kept per author     :", limits["per_author"])
    print("  Pages per watchlist :", limits["pages_per_watchlist"])
    print("  Pause between asks  : %.1f seconds" % limits["delay"])
    print("  Only new posts      :", "yes" if only_new else "no, full search")
    print("  Worst case cost     : about $%.4f" % estimate)
    if price_warning:
        print(price_warning)
    print("-" * 62)
    for line in spend.describe(daily_cap, price_per_1000):
        print(line)
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

    # The ledger must be written whatever happens. If a later
    # watchlist fails, the posts already received from earlier ones
    # have still been paid for, and a cap that forgets them is not a
    # cap. A finally block covers a supplier error, a Ctrl and C, and
    # a clean finish alike.
    try:
        for watchlist in watchlists:
            name = watchlist.get("name", "unnamed")
            print("")
            print("Searching:", name)

            if state["received"] >= limits["per_run"]:
                print("  Stopped. The run wide cap was already reached.")
                reports.append({
                    "name": name,
                    "received": 0, "saved": 0, "duplicates": 0,
                    "author_capped": 0, "pages": 0, "incremental": False,
                    "reached_supplier": False,
                    "note": "Skipped, the run wide cap was already reached.",
                })
                continue

            since_time = since_time_for(watchlist, memory, overlap, not only_new)
            query = (watchlist.get("query") or "").strip()

            if since_time is None:
                print("  Looking at all recent posts.")
            else:
                looking_back = datetime.fromtimestamp(since_time, timezone.utc)
                print("  Looking only at posts since",
                      looking_back.strftime("%d %b %H:%M UTC"))

            try:
                report = collect_watchlist(watchlist, limits, state,
                                           source, query, since_time)
            except sources.SourceError as error:
                stop(str(error))
            reports.append(report)

            # Move the watermark forward only if the supplier actually
            # answered. If the request failed we must look again from the
            # same point next time, or those posts are lost for good.
            if report["reached_supplier"]:
                memory["watchlists"][name] = {
                    "checked_at": run_started,
                    "query": query,
                }

            print("  Received from supplier :", report["received"])
            print("  New posts saved        :", report["saved"])
            print("  Already had            :", report["duplicates"])
            print("  Held back, author cap  :", report["author_capped"])
            print("  Pages requested        :", report["pages"])
            print("  ", report["note"])

    finally:
        save_posts(state["saved"])
        save_collect_state(memory)
        spend.record(state["received"], source=arguments.source)

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
    print("-" * 62)
    for line in spend.describe(daily_cap, price_per_1000):
        print(line)
    print("=" * 62)

    # Three very different reasons for an empty watchlist, and they
    # must never be confused. Nothing new is good news. Nothing at all
    # is a query problem. A failed request is a real fault.
    failed = [r["name"] for r in reports
              if r["pages"] > 0 and not r["reached_supplier"]]
    quiet = [r["name"] for r in reports
             if r["received"] == 0 and r["reached_supplier"] and r["incremental"]]
    empty = [r["name"] for r in reports
             if r["received"] == 0 and r["reached_supplier"]
             and not r["incremental"]]

    if quiet:
        print("")
        print("Nothing new since the last check for:")
        for name in quiet:
            print("  -", name)
        print("That is the correct result, not a fault. Nothing arrived,")
        print("so nothing was charged.")

    if empty:
        print("")
        print("These watchlists matched nothing at all:")
        for name in empty:
            print("  -", name)
        print("That is a query problem. Open config.yaml and loosen the")
        print("query, for example by removing one required phrase.")

    if failed:
        print("")
        print("These watchlists could not be reached:")
        for name in failed:
            print("  -", name)
        print("Nothing was charged for them, and their memory was left")
        print("where it was, so the next run picks up the same window")
        print("again. No posts are lost. If this keeps happening, check")
        print("your internet connection and your API key.")

    print("")
    print("All later steps read from that file, so they cost nothing.")


if __name__ == "__main__":
    main()
