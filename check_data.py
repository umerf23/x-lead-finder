"""
A checker, not a fixer.

Purpose: look inside the data folder and say, in plain English, why the
digest is empty. It changes nothing and costs nothing.

Run it:
    python check_data.py                  just look
    python check_data.py --forget-sent    let the digest show old leads again
"""

import argparse
import json
import os

DATA_FOLDER = "data"
POSTS_FILE = os.path.join(DATA_FOLDER, "posts.json")
SCORED_FILE = os.path.join(DATA_FOLDER, "scored.json")
STATE_FILE = os.path.join(DATA_FOLDER, "watch_state.json")

# The same names digest.py looks for, in the same order.
SCORE_KEYS = ("score", "intent_score", "lead_score", "rating")
REASON_KEYS = ("reason", "why", "explanation", "justification")
BUDGET_KEYS = ("budget_signal", "budget_mentioned", "money_mentioned",
               "has_budget", "budget")

LINE = "-" * 60


def read_json(path):
    """Return the contents, or None with a reason printed."""
    if not os.path.exists(path):
        print("  Missing. There is no %s yet." % path)
        return None
    try:
        with open(path, "r", encoding="utf-8") as handle:
            return json.load(handle)
    except ValueError as error:
        print("  Unreadable. The file exists but is not valid JSON.")
        print("  Detail: %s" % error)
        return None
    except OSError as error:
        print("  Could not be opened. Detail: %s" % error)
        return None


def as_records(loaded):
    """Accept either a plain list or a list wrapped inside an object."""
    if isinstance(loaded, dict):
        for value in loaded.values():
            if isinstance(value, list):
                loaded = value
                break
    if not isinstance(loaded, list):
        return []
    return [item for item in loaded if isinstance(item, dict)]


def matching_key(record, keys):
    for key in keys:
        if record.get(key) not in (None, ""):
            return key
    return None


def to_number(value):
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return None


def forget_sent():
    """Clear the memory of which leads already went out."""
    if not os.path.exists(STATE_FILE):
        print("There is no watch_state.json, so there is nothing to forget.")
        return
    try:
        with open(STATE_FILE, "r", encoding="utf-8") as handle:
            state = json.load(handle)
    except (ValueError, OSError):
        state = {}
    if not isinstance(state, dict):
        state = {}
    removed = len(state.get("digested_ids") or [])
    state["digested_ids"] = []
    state.pop("last_digest_date", None)
    with open(STATE_FILE, "w", encoding="utf-8") as handle:
        json.dump(state, handle, indent=2, ensure_ascii=False)
    print("Forgot %d leads that had already been sent." % removed)
    print("Your daily spend count was left alone.")
    print("Now run: python digest.py --preview")


def main():
    parser = argparse.ArgumentParser(
        description="Explain why the digest is empty.")
    parser.add_argument("--forget-sent", action="store_true", dest="forget",
                        help="Let already sent leads appear in the digest again.")
    arguments = parser.parse_args()

    if arguments.forget:
        forget_sent()
        return

    print(LINE)
    print("DATA CHECK")
    print(LINE)

    print("\n1. Collected posts, %s" % POSTS_FILE)
    posts = as_records(read_json(POSTS_FILE))
    if posts:
        print("  Found %d posts." % len(posts))

    print("\n2. Scored posts, %s" % SCORED_FILE)
    scored = as_records(read_json(SCORED_FILE))
    if not scored:
        print("  No usable records. Run: python score.py")
        print(LINE)
        return
    print("  Found %d records." % len(scored))

    sample = scored[0]
    print("\n3. Field names in the first record")
    for key in sorted(sample.keys()):
        preview = str(sample[key])
        if len(preview) > 45:
            preview = preview[:45] + " ..."
        print("  %-18s %s" % (key, preview))

    print("\n4. What the digest is looking for")
    score_key = matching_key(sample, SCORE_KEYS)
    reason_key = matching_key(sample, REASON_KEYS)
    budget_key = matching_key(sample, BUDGET_KEYS)
    print("  Score field   : %s" % (score_key or "NOT FOUND"))
    print("  Reason field  : %s" % (reason_key or "not found, not fatal"))
    print("  Budget field  : %s" % (budget_key or "not found, not fatal"))

    if not score_key:
        print("\n  This is your problem. The digest cannot see a score.")
        print("  Look at the field list above, find the one holding the")
        print("  number out of 100, and tell me its exact name.")
        print(LINE)
        return

    print("\n5. Score spread")
    numbers = [to_number(record.get(score_key)) for record in scored]
    usable = [number for number in numbers if number is not None]
    if not usable:
        print("  The score field exists but holds no readable numbers.")
        print(LINE)
        return
    bands = {
        "85 and above": len([n for n in usable if n >= 85]),
        "75 to 84": len([n for n in usable if 75 <= n < 85]),
        "65 to 74": len([n for n in usable if 65 <= n < 75]),
        "below 65": len([n for n in usable if n < 65]),
    }
    for label, count in bands.items():
        print("  %-14s %d" % (label, count))
    print("  Highest score : %d" % max(usable))
    passing = len([n for n in usable if n >= 65])

    print("\n6. Already sent")
    state = read_json(STATE_FILE)
    sent = []
    if isinstance(state, dict):
        sent = state.get("digested_ids") or []
        print("  %d leads have gone out in a previous digest." % len(sent))
        print("  Last digest date: %s" % (state.get("last_digest_date") or "none"))
    else:
        print("  No record of any digest being sent yet.")

    sent_set = set(str(item) for item in sent)
    fresh = 0
    for record in scored:
        number = to_number(record.get(score_key))
        if number is not None and number >= 65:
            if str(record.get("post_id") or "") not in sent_set:
                fresh += 1

    print("\n" + LINE)
    print("VERDICT")
    print(LINE)
    if passing == 0:
        print("Nothing scores 65 or above, so the digest is correctly empty.")
        print("Your filter is working, the leads simply are not there yet.")
        print("Either collect more posts, or lower digest_min_score in")
        print("config.yaml to see more.")
    elif fresh == 0:
        print("%d leads pass the threshold, but all of them have already" % passing)
        print("been sent, so the digest has nothing new to show.")
        print("To see them again, run: python check_data.py --forget-sent")
    else:
        print("%d leads should appear in your next digest." % fresh)
        print("If the page still looks empty, run: python digest.py --preview")
        print("then: start data\\digest.html")
    print(LINE)


if __name__ == "__main__":
    main()
