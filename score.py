"""
Step 8c scorer.

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

What changed in Step 8c, and why:

Three real posts scored 95 when they should not have.

- A job board relaying somebody else's vacancy scored 95, because
  the old rules told the model to assume buyer whenever it was
  unsure, and because the account handle was never sent to the
  model. It could not see it was reading @YTJobsSpotlight.
- Two comedy posts about a video editor being abducted by Elon Musk
  scored 95 each, because nothing in the rules said a post can be a
  joke.
- Both jokes received word for word the same reason, which is the
  tell that the model had stopped reading and started guessing.

The fixes:

1. The account handle and display name now go to the model, so it
   can apply the relay test at all.
2. A reality test runs first. Jokes, stories, and hypotheticals
   score below 20 no matter how much hiring language they contain.
3. An evidence test runs last. The model must quote the exact words
   from the post that prove someone wants to pay for work. The code
   then checks that quote really appears in the post. If it does
   not, the score is capped at 39 automatically. A model cannot
   invent its way past a string comparison.
"""

import json
import os
import re
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

# The highest score a post may keep when its evidence cannot be found
# in the post itself. Just under the 65 threshold, so an unproven
# claim never reaches your digest.
UNPROVEN_SCORE_CAP = 39

# How much of the quoted evidence must appear in the post before we
# accept it. A model may tidy punctuation or fix a typo while quoting,
# so an exact match is too strict, but seven words in ten is a fair
# test of whether it is quoting or inventing.
EVIDENCE_WORD_MATCH = 0.7

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

Apply three tests, in this order. A post must pass all three.

TEST ONE, IS IT REAL

Is this a sincere post about real work the author actually wants
done? Many posts use hiring language without being about hiring.

Score below 20, category not relevant, if the post is any of these:
- a joke, a punchline, a meme, or a bit of comedy
- a story about something that already happened, told for amusement
- satire, sarcasm, or exaggeration for effect
- a hypothetical, a thought experiment, or a what if
- engagement bait, a poll, a quiz, or a hot take
- an announcement about the author's own product or launch

Warning signs: an impossible or absurd event, a twist ending, past
tense narration about an employee who left, humour at somebody's
expense, a punchline in the final sentence.

A post can mention editors, hiring, Slack, and AI, and still be a
joke. Read the whole post to the end before you decide. If the last
line is funny, the post was a joke.

TEST TWO, WHO IS THE BUYER

Ask who benefits from the work being done.

If the answer is the account posting, or the company that account
directly speaks for, it is a buyer and it is a lead.

If the account is passing along somebody else's vacancy, it is a
relay. Score relays 20 to 39, whatever the underlying job is worth.
The job may be entirely real. It is simply not this account's job,
and there is nobody here to sell to.

Treat the account as a relay when any of these are true:
- the handle or display name contains jobs, job, hiring, hire,
  recruit, recruiting, recruiter, careers, talent, spotlight,
  vacancies, or a similar word about listing work
- the post names the employer in the third person and then says
  what that employer wants, for example a channel name, a brand
  name, our client, or a fast growing startup
- the post is mostly a link to an external application page
- the post reads as one entry in a stream of unrelated vacancies

Worked relay example: a post reading that SomeChannel with 31.9K
subscribers is looking for a part time editor, details and apply at
a link, is a relay. The account is describing somebody else's
vacancy in the third person. Score about 25.

If the account wants to be paid rather than to pay, it is a seller.
Score below 20.

If you genuinely cannot tell whether an account is a buyer or a
relay, score between 40 and 64 and use the category unclear. Do not
guess upward. A human reviews everything above 65, so putting an
unproven lead there wastes the reviewer's trust, which is the one
thing this tool cannot afford to lose.

TEST THREE, WHERE IS THE PROOF

Copy the exact words from the post that show someone wants work
done and would pay for it. Put them in the evidence field, word for
word, at most 20 words.

You may not paraphrase. You may not infer. You may not summarise.
If the words are not in the post, there is no evidence.

If you cannot find such words, you may not score above 39.

SCORING GUIDE, for posts that pass all three tests

90 to 100  a clear current request to hire, commission, contract, or
           pay someone, including a company advertising a role for
           work it needs done itself
70 to 89   asking for a recommendation, a referral, or someone who
           can build a thing, without a firm commitment yet
40 to 69   describes a need or a problem but makes no request, or a
           post you cannot confidently classify
20 to 39   commentary, curiosity, a news reaction, or a vacancy
           relayed on behalf of somebody else
0 to 19    selling their own services, self promotion, a tutorial,
           looking for work themselves, jokes, sarcasm, bait

A company or individual hiring for their own project is a genuine
lead even when the post reads like a formal job advertisement.
Phrases such as we are hiring, join our team, or apply here do not
disqualify a post. What matters is that the work belongs to the
account posting, and that the account is not a jobs account.

BUDGET SIGNAL

Set budget_signal to true only when the post mentions money that
this account would pay for this work: a rate, a budget, a salary
they are offering, or the word paid.

Set it to false for the price of the author's own product, for
currencies or tokens they accept from customers, and for money they
hope to receive. Money flowing towards the author is not a budget.

CATEGORY LABEL

Choose exactly one, spelled as shown:
direct hire
seeking referral
need stated
job board relay
self promotion
unclear
not relevant

OUTPUT

Return only JSON. No explanation, no markdown, no code fences.
Return a list, one object per post, in this exact shape:

[
  {
    "post_id": "the id you were given",
    "intent_score": 0,
    "category": "one of the seven labels above",
    "reason": "one short sentence naming the buyer and the work",
    "evidence": "exact words copied from the post, or empty",
    "budget_signal": false
  }
]

The reason is read by a human deciding whether to trust the score,
so it must say who wants what. Never give two different posts the
same reason. If you find yourself repeating a sentence, you have
stopped reading the post in front of you.
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
    """
    Describe each post to the model.

    The account handle and display name are included deliberately.
    Without them the model cannot apply the relay test, because it
    cannot see that it is reading a jobs account.
    """
    lines = []
    for post in batch:
        watchlist = post.get("watchlist", "")
        context = descriptions.get(watchlist, "")
        text = (post.get("text") or "").replace("\n", " ").strip()
        lines.append(
            "post_id: " + post.get("post_id", "") + "\n"
            "account handle: @" + (post.get("author") or "unknown") + "\n"
            "account display name: " + (post.get("author_name") or "") + "\n"
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
                print("  Hit the free tier speed limit. Waiting",
                      wait, "seconds.")
                time.sleep(wait)
                continue
            if "401" in message or "API_KEY" in message.upper():
                stop("Your Google key was rejected. "
                     "Check GEMINI_API_KEY in your .env file.")
            print("  The model could not be reached. Details:", message[:200])
            return None

        parsed = parse_reply(response)
        if parsed is not None:
            return parsed
        print("  The reply was not readable. Retrying.")
        time.sleep(3)

    print("  Gave up on this batch after", MAX_RETRIES, "tries.")
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
# Checking the model's answer against the post
# ---------------------------------------------------------------------

def normalise(text):
    """Lower case, strip punctuation, squeeze spaces. For comparing only."""
    cleaned = re.sub(r"[^a-z0-9\s]", " ", str(text or "").lower())
    return " ".join(cleaned.split())


def evidence_supported(evidence, post_text):
    """
    Decide whether the quoted evidence really came from the post.

    This is the part a model cannot talk its way around. It either
    quoted the post or it did not, and a string comparison settles it.

    An exact match is preferred. Failing that, seven words in ten must
    appear in the post, which allows for tidied punctuation without
    allowing invention.
    """
    quote = normalise(evidence)
    body = normalise(post_text)
    if not quote:
        return False
    if quote in body:
        return True

    words = quote.split()
    if not words:
        return False
    body_words = set(body.split())
    found = sum(1 for word in words if word in body_words)
    return (found / len(words)) >= EVIDENCE_WORD_MATCH


def safe_score(value):
    try:
        number = int(float(value))
    except (TypeError, ValueError):
        return 0
    return max(0, min(100, number))


def merge_judgement(post, judgement):
    """
    Combine a post with its judgement, and enforce the evidence rule.

    The model is asked to quote its proof. Here we check the quote is
    real. If it is not, the score is capped below the review threshold
    and the record says plainly that it was capped, so nothing is
    silently altered behind your back.
    """
    combined = dict(post)
    score = safe_score(judgement.get("intent_score"))
    evidence = str(judgement.get("evidence", "") or "").strip()[:300]
    proven = evidence_supported(evidence, post.get("text", ""))

    combined["evidence"] = evidence
    combined["evidence_verified"] = proven
    combined["score_capped"] = False

    if score > UNPROVEN_SCORE_CAP and not proven:
        combined["score_capped"] = True
        combined["original_score"] = score
        score = UNPROVEN_SCORE_CAP

    combined["intent_score"] = score
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
        print("To judge it all again with the current rules, run:")
        print("  python score.py --fresh")
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
            print("  Skipped this batch.")
            continue

        by_id = {}
        for item in judgements:
            if isinstance(item, dict):
                key = str(item.get("post_id", ""))
                if key:
                    by_id[key] = item

        matched = 0
        capped = 0
        for post in batch:
            judgement = by_id.get(post.get("post_id"))
            if judgement is None:
                continue
            record = merge_judgement(post, judgement)
            scored.append(record)
            matched += 1
            if record["score_capped"]:
                capped += 1

        print("  Scored", matched, "of", len(batch), "posts in this batch.")
        if capped:
            print("  Held back", capped,
                  "because the quoted evidence was not in the post.")

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
    capped = [row for row in ranked if row.get("score_capped")]

    print("")
    print("Posts scoring 65 or above:", len(strong), "out of", len(ranked))
    if capped:
        print("Held back for unproven evidence:", len(capped))
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
        if row.get("evidence"):
            print("Evidence :", row.get("evidence", "")[:120])
        print("Link     :", row.get("post_url", ""))
        print("-" * 62)


if __name__ == "__main__":
    main()
