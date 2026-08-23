"""
Step 6 scorer.

Purpose: read the posts already saved by collect.py, ask an AI model
to judge how likely each author is to be actively looking to pay
someone for work, and save the results.

This spends no X credits. It only reads data/posts.json.
Posts that were scored on an earlier run are skipped, so running it
again is free and fast.

Run it with:

    python score.py

To throw away every previous score and judge the whole store again
with the current rules, run:

    python score.py --fresh

That is what you want after changing the judging rules. It is still
free, because it only reads the local file.
"""

import json
import os
import sys
import time

import yaml
from dotenv import load_dotenv
from google import genai
from google.genai import types

# ---------------------------------------------------------------------
# Setup
# ---------------------------------------------------------------------

CONFIG_FILE = "config.yaml"
DATA_FOLDER = "data"
POSTS_FILE = os.path.join(DATA_FOLDER, "posts.json")
SCORED_FILE = os.path.join(DATA_FOLDER, "scored.json")

MODEL_NAME = "gemini-2.5-flash"
BATCH_SIZE = 20
SECONDS_BETWEEN_BATCHES = 5
MAX_RETRIES = 3

load_dotenv()
GEMINI_KEY = os.getenv("GEMINI_API_KEY")


def stop(message):
    print(message)
    raise SystemExit(1)


# ---------------------------------------------------------------------
# The instructions given to the model
# ---------------------------------------------------------------------

SYSTEM_INSTRUCTION = """
You sort public social media posts for a freelance lead pipeline.

For every post, decide one thing only: how likely is it that the
account posting it would pay somebody to do this work.

THE BUYER TEST, apply this first
Ask who benefits from the work being done.
  If the answer is the account posting, or the company that account
  speaks for, then it is a buyer and it is a lead.
  If the answer is somebody else, and the account is only passing
  along a request that is not its own, then it is a relay and it is
  not a lead.
  If the account wants to be paid rather than to pay, it is a seller
  and it is not a lead.

Scoring guide
  90 to 100  a clear current request to hire, commission, contract,
             or pay someone, including a company advertising a role
             for work it needs done itself
  70 to 89   asking for a recommendation, a referral, or someone who
             can build a thing, without a firm commitment yet
  40 to 69   describes a need or a problem but makes no request
  20 to 39   commentary, curiosity, a news reaction, or a vacancy
             relayed on behalf of somebody else
   0 to 19   selling their own services, self promotion, a tutorial,
             looking for work themselves, sarcasm, engagement bait

RULES THAT OVERRIDE THE GUIDE

1. A company or an individual hiring for their own project is a
   genuine lead, even when the post reads like a formal job
   advertisement. Phrases such as we are hiring, join our team, now
   recruiting, or apply here do not disqualify a post. What matters
   is that the work belongs to the account posting. Score these on
   the normal guide, which usually puts them at 90 or above.

2. Only relays score below 30. A relay is a recruitment agency, a
   job board, a jobs aggregator, or any account whose purpose is
   listing other people's vacancies. Signs of a relay include
   describing the employer in the third person, such as our client
   or a fast growing startup, posting many unrelated roles, an
   account name built around jobs or hiring, or a post that is
   mostly a link to an external listing with no personal voice.

3. Anyone offering their own services scores below 20, however much
   the wording resembles a request. Someone writing that they build
   AI workflows and to send them a message is a competitor, not a
   customer.

4. A purely technical how-to question, with no sign of wanting to
   hand the work over, scores below 40.

5. When you cannot tell whether an account is a direct buyer or a
   relay, treat it as a direct buyer and say so in the reason. A
   human reviews every result, so a missed lead costs more than a
   wrong one.

WORKED EXAMPLES, follow these closely

  "We are a skincare brand and we need an AI UGC editor for our ad
  creative, ongoing, paid monthly."
  Score about 95. The brand needs the work for itself. A formal
  tone does not make it a relay.

  "Our client, a fast growing DTC brand, seeks an AI video editor.
  Apply through the link."
  Score about 20. The account speaks for somebody else and there is
  nobody here to sell to.

  "Anyone know someone good who can wire up an n8n workflow for my
  agency? Happy to pay."
  Score about 92. Direct buyer, money mentioned.

  "Struggling to keep up with editing all these UGC ads by hand."
  Score about 55. A real need, but no request has been made.

  "I build custom AI workflows for agencies. Portfolio in bio, DM
  me."
  Score about 5. Selling, not buying.

CATEGORY LABEL
Choose exactly one of these, spelled as shown:
  direct hire
  seeking referral
  need stated
  job board relay
  self promotion
  not relevant

Return only JSON. No explanation, no markdown, no code fences.
Return a list, one object per post, in this exact shape:

[
  {
    "post_id": "the id you were given",
    "intent_score": 0,
    "category": "one of the six labels above",
    "reason": "one short sentence naming the buyer and the work",
    "budget_signal": false
  }
]

The reason is read by a human deciding whether to trust the score,
so it must say who wants what, not merely restate the score.

Set budget_signal to true only if the post mentions money, a budget,
a rate, or payment.
"""


# ---------------------------------------------------------------------
# Loading files
# ---------------------------------------------------------------------

def load_json_file(path, default):
    if not os.path.exists(path):
        return default

    try:
        with open(path, "r", encoding="utf-8") as handle:
            return json.load(handle)
    except (ValueError, OSError):
        print("Warning: could not read", path, "so starting fresh.")
        return default


def save_json_file(path, content):
    os.makedirs(DATA_FOLDER, exist_ok=True)
    with open(path, "w", encoding="utf-8") as handle:
        json.dump(content, handle, indent=2, ensure_ascii=False)


def load_descriptions():
    """Pull the plain English description of each watchlist from config."""
    descriptions = {}

    if not os.path.exists(CONFIG_FILE):
        return descriptions

    try:
        with open(CONFIG_FILE, "r", encoding="utf-8") as handle:
            config = yaml.safe_load(handle) or {}
    except yaml.YAMLError:
        return descriptions

    for watchlist in config.get("watchlists") or []:
        name = watchlist.get("name")
        description = (watchlist.get("description") or "").strip()
        if name:
            descriptions[name] = description

    return descriptions


# ---------------------------------------------------------------------
# Talking to the model
# ---------------------------------------------------------------------

def build_prompt(batch, descriptions):
    lines = []

    for post in batch:
        watchlist = post.get("watchlist", "")
        context = descriptions.get(watchlist, "")
        text = (post.get("text") or "").replace("\n", " ").strip()

        lines.append(
            "post_id: " + post.get("post_id", "") + "\n"
            "we are watching for: " + (context or watchlist) + "\n"
            "post text: " + text[:600] + "\n"
        )

    return "Judge these posts.\n\n" + "\n---\n".join(lines)


def clean_reply(raw):
    """Strip code fences if the model adds them despite instructions."""
    text = (raw or "").strip()

    if text.startswith("```"):
        text = text.split("\n", 1)[-1]
        if text.rstrip().endswith("```"):
            text = text.rstrip()[:-3]

    return text.strip()


def judge_batch(client, batch, descriptions):
    prompt = build_prompt(batch, descriptions)

    settings = types.GenerateContentConfig(
        system_instruction=SYSTEM_INSTRUCTION,
        response_mime_type="application/json",
        temperature=0.1,
    )

    for attempt in range(1, MAX_RETRIES + 1):
        try:
            response = client.models.generate_content(
                model=MODEL_NAME,
                contents=prompt,
                config=settings,
            )
        except Exception as error:
            message = str(error)

            if "429" in message or "RESOURCE_EXHAUSTED" in message:
                wait = attempt * 20
                print("   Hit the free tier speed limit. Waiting",
                      wait, "seconds.")
                time.sleep(wait)
                continue

            if "401" in message or "API_KEY" in message.upper():
                stop("Your Google key was rejected. "
                     "Check GEMINI_API_KEY in your .env file.")

            print("   The model could not be reached. Details:", message[:200])
            return None

        parsed = parse_reply(response)
        if parsed is not None:
            return parsed

        print("   The reply was not readable. Retrying.")
        time.sleep(3)

    print("   Gave up on this batch after", MAX_RETRIES, "tries.")
    return None


def parse_reply(response):
    raw = getattr(response, "text", None)
    text = clean_reply(raw)

    if not text:
        return None

    try:
        result = json.loads(text)
    except ValueError:
        return None

    if isinstance(result, dict):
        for key in ("results", "posts", "items"):
            if isinstance(result.get(key), list):
                result = result[key]
                break

    if not isinstance(result, list):
        return None

    return result


# ---------------------------------------------------------------------
# Tidying the model's answer
# ---------------------------------------------------------------------

def safe_score(value):
    try:
        number = int(float(value))
    except (TypeError, ValueError):
        return 0

    return max(0, min(100, number))


def merge_judgement(post, judgement):
    combined = dict(post)
    combined["intent_score"] = safe_score(judgement.get("intent_score"))
    combined["category"] = str(judgement.get("category", "") or "unsorted")[:40]
    combined["reason"] = str(judgement.get("reason", "") or "")[:200]
    combined["budget_signal"] = bool(judgement.get("budget_signal", False))
    combined["status"] = post.get("status", "new")
    return combined


# ---------------------------------------------------------------------
# Main routine
# ---------------------------------------------------------------------

def main():
    if not GEMINI_KEY:
        stop("No Google key found. Open .env and add the line\n"
             "GEMINI_API_KEY=your_key_here")

    posts = load_json_file(POSTS_FILE, [])

    if not posts:
        stop("No posts found. Run collect.py first to gather some.")

    fresh = "--fresh" in sys.argv

    if fresh and os.path.exists(SCORED_FILE):
        os.remove(SCORED_FILE)
        print("Fresh mode: previous scores cleared, every post will be")
        print("judged again with the current rules. This still costs nothing.")
        print("")

    scored = load_json_file(SCORED_FILE, [])
    already_done = {row.get("post_id") for row in scored}

    pending = [p for p in posts if p.get("post_id") not in already_done]

    print("=" * 62)
    print("SCORING PLAN")
    print("=" * 62)
    print("  Posts in storage      :", len(posts))
    print("  Already scored        :", len(already_done))
    print("  To be scored now      :", len(pending))
    print("  Cost                  : nothing, this uses the free tier")
    print("=" * 62)

    if not pending:
        print("")
        print("Everything is already scored. Nothing to do.")
        show_results(scored)
        return

    try:
        client = genai.Client(api_key=GEMINI_KEY)
    except Exception as error:
        stop("Could not start the AI client. Details: " + str(error)[:200])

    descriptions = load_descriptions()

    batches = [pending[i:i + BATCH_SIZE]
               for i in range(0, len(pending), BATCH_SIZE)]

    for number, batch in enumerate(batches, start=1):
        print("")
        print("Judging batch", number, "of", len(batches),
              "(", len(batch), "posts )")

        judgements = judge_batch(client, batch, descriptions)

        if judgements is None:
            print("   Skipped this batch.")
            continue

        by_id = {}
        for item in judgements:
            if isinstance(item, dict):
                key = str(item.get("post_id", ""))
                if key:
                    by_id[key] = item

        matched = 0
        for post in batch:
            judgement = by_id.get(post.get("post_id"))

            if judgement is None:
                continue

            scored.append(merge_judgement(post, judgement))
            matched += 1

        print("   Scored", matched, "of", len(batch), "posts in this batch.")

        save_json_file(SCORED_FILE, scored)

        if number < len(batches):
            time.sleep(SECONDS_BETWEEN_BATCHES)

    save_json_file(SCORED_FILE, scored)

    print("")
    print("=" * 62)
    print("SCORING COMPLETE")
    print("=" * 62)
    print("  Total scored posts    :", len(scored))
    print("  Saved to              :", SCORED_FILE)
    print("=" * 62)

    show_results(scored)


def show_results(scored):
    if not scored:
        return

    ranked = sorted(scored,
                    key=lambda row: row.get("intent_score", 0),
                    reverse=True)

    strong = [row for row in ranked if row.get("intent_score", 0) >= 65]

    print("")
    print("Posts scoring 65 or above:", len(strong),
          "out of", len(ranked))
    print("")
    print("TOP RESULTS")
    print("-" * 62)

    for row in ranked[:8]:
        print("Score    :", row.get("intent_score", 0),
              "  Budget mentioned:", row.get("budget_signal", False))
        print("Category :", row.get("category", ""))
        print("Author   : @" + row.get("author", ""))
        print("Post     :", (row.get("text") or "")[:160].replace("\n", " "))
        print("Reason   :", row.get("reason", ""))
        print("Link     :", row.get("post_url", ""))
        print("-" * 62)


if __name__ == "__main__":
    main()
