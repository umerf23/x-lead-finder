# X Lead Finder

Finds people on X who are actively looking to pay someone for AI UGC
video, custom AI workflows, or automation work, and puts them in a
reviewable list with a score, a reason, and the author's own words as
proof.

No automatic outreach. Nothing is ever sent on your behalf.

---

## 1. What it does

It works in two stages, and the second one is the point.

**Stage one, a cheap wide net.** X advanced search pulls posts matching
your keywords. This is the part that costs money, so it is capped,
counted, and never asked for the same post twice.

**Stage two, an AI judge.** Every post collected is read by Google
Gemini, which scores it out of 100 for how likely the author is to pay
somebody for this work. It returns a category, a one line reason, and a
quote from the post proving its answer.

Keyword search alone returns mostly noise. An AI reading every post on
X would be unaffordable. Running them in that order means the expensive
judgement only ever sees pre-filtered posts, which cuts the AI cost by
roughly ninety five percent while removing about eighty five percent of
the noise.

### The evidence check

The model is required to quote the exact words that prove intent. The
program then checks that quote really appears in the post. If it does
not, the score is capped below the review threshold and the lead is
marked "score held back".

This matters because a model can invent a convincing reason. It cannot
invent its way past a string comparison. On a real test run this caught
a job board relaying somebody else's vacancy that had been scored 95,
and dropped it to 25.

### What you see

A local web app at `http://127.0.0.1:8000` with four tabs.

- **Leads.** Every judged post, best first, with filters for score,
  watchlist, category, review state, keywords, money mentioned, and one
  post per author. Save and Mark handled are remembered permanently.
- **Describe.** Type a plain English sentence such as *people looking
  for someone to build a custom AI workflow*. The model turns it into a
  working X search query, warns you about the mistakes that reliably
  return nothing, lets you test it against real posts, and saves it as a
  new watchlist. No code, no query syntax to learn.
- **Settings.** Edit watchlists and spending limits in the browser.
  config.yaml is never opened by hand.
- **Collect.** Run a collection, score new posts, or re-judge
  everything under the current rules. The cost is shown and confirmed
  before anything is spent.

There is also an unattended mode. `python watch.py` collects, scores
and refreshes on a timer, and once a day sends a digest of the best new
leads to Slack or email. A tool is judged by whether it reaches you
without being opened.

---

## 2. Install, in five commands

You need Python 3.10 or newer, and two free API keys.

Open PowerShell in the project folder and run these one at a time.

```
python -m venv venv
.\venv\Scripts\Activate.ps1
pip install -r requirements.txt
copy .env.example .env
python app.py
```

Then open `http://127.0.0.1:8000` in your browser.

Between the fourth and fifth command, open `.env` and paste in your two
keys. Where to get them:

- **TWITTER_API_KEY** from twitterapi.io. Sign in with Google and you
  get free starter credits, no card required.
- **GEMINI_API_KEY** from aistudio.google.com. Permanently free, no
  card required, roughly 1500 requests a day.

If PowerShell refuses to run the activate script, run this once and try
again:

```
Set-ExecutionPolicy -Scope CurrentUser RemoteSigned
```

To stop the app, press Ctrl and C in the PowerShell window.

---

## 3. How to change what it watches

Open the app, click **Settings**, and edit the watchlists there. Each
one has:

- a **name** you will recognise
- a **description** in plain English, which the AI judge reads to decide
  what counts as a match
- a **query**, the search sent to X
- an on and off switch

Or use the **Describe** tab and let the model write the query for you.

Everything is saved to `config.yaml`, with the previous version kept as
`config.backup.yaml`. You never need to touch the file directly, and if
you do, do not use Notepad, which mangles the spacing.

### Writing a query that works

The single most common mistake is asking for too much at once. Every
extra required phrase multiplies the chance of matching nothing.

Use two required groups and no more:

```
(topic words OR joined OR by OR OR) ("looking for" OR "need someone" OR hiring) lang:en -filter:retweets
```

Forty loosely relevant posts beat zero perfect ones, because the AI
judge reads every result and throws the noise away. The app warns you
when a query breaks this rule.

### Other settings worth knowing

| Setting | What it does |
| --- | --- |
| `daily_post_cap` | Hard daily limit on posts bought, shared by every route |
| `max_posts_per_run` | Ceiling for a single collection |
| `max_posts_per_author` | Stops one loud account filling your results |
| `only_new_posts` | Asks only for posts newer than the last check |
| `watcher.poll_every_minutes` | How often unattended mode collects |
| `watcher.digest_at` | What time the daily summary is sent |

---

## 4. What it costs per month

Data comes from twitterapi.io at about $0.15 per 1000 posts received.
The AI judging is free: Google's free tier covers roughly 1500 requests
a day, and posts are judged twenty at a time, so 30,000 posts a day
would still fit.

You pay for posts **received**, not posts kept.

| Usage | Posts per day | Per month | Cost per month |
| --- | --- | --- | --- |
| Light, one watchlist checked hourly | 200 | 6,000 | about $0.90 |
| Steady, three watchlists on a ten minute cycle | 1,000 | 30,000 | about $4.50 |
| Heavy, continuous monitoring across many topics | 5,000 | 150,000 | about $22.50 |

For comparison, the official X API charges about $0.005 per post read.
The same light usage would cost roughly $30 a month there, and the
tiers that allow real time streaming start at $5,000 a month.

Run `python spend.py` at any time to see what today and this month have
actually cost.

---

## What you should know before relying on it

Four things stated plainly, because you will find them out eventually
and it is better to hear them now.

**This polls, it does not stream.** True real time streaming on X is
restricted to plans starting at $5,000 a month. This checks every few
minutes instead. In practice a lead posted at 10:00 reaches you by
10:10, which for freelance outreach is fast enough.

**The data supplier scrapes rather than licenses.** twitterapi.io is a
third party and its collection method sits against X's terms of
service. It is used here because the official API has no free tier for
new developers as of February 2026. The data source is designed to be
swappable, so an organisation that needs strict compliance can move to
the official API without changing anything else.

**Google's free tier may use prompts for training.** Only public posts
are sent, never your keys, your notes, or anything private. If that is
still unacceptable, a paid Gemini key removes it with no code change.

**The AI is a filter, not an oracle.** It is right most of the time and
wrong some of the time, which is why every score carries a reason and a
verified quote, and why nothing is ever contacted automatically. A
human reads the list. The tool's job is to make that list short enough
to read.

---

## The files, briefly

| File | What it is |
| --- | --- |
| `app.py` | The web app. Start here. |
| `dashboard_app.html` | The page the app serves |
| `collect.py` | Buys posts from the supplier. The only file that spends |
| `score.py` | Judges posts with Gemini. Free |
| `query_builder.py` | Turns plain English into a search query |
| `spend.py` | The shared spending ledger |
| `watch.py` | Unattended mode, on a timer |
| `digest.py` | The daily summary, to Slack or email |
| `check_data.py` | Explains why a digest is empty. Changes nothing |
| `config.yaml` | Every setting. Editable in the browser |
| `data/` | Your posts, scores, review marks and ledger. Stays local |
