"""
Step 10 query builder.

Purpose: let somebody describe in ordinary words the kind of person they
want to find, and turn that into a working X advanced search query plus
the plain English description that the scorer reads.

For example, typing this:

    people looking for someone to build a custom AI workflow

produces a watchlist with a name, a description, and a query along the
lines of:

    ("AI workflow" OR "AI agent" OR n8n OR Zapier OR automation)
    ("looking for" OR "need someone" OR "who can build" OR hiring)
    lang:en -filter:retweets

Nothing is saved until you approve it.

This module can be used two ways. The web app imports it, and you can
also run it on its own from PowerShell:

    python query_builder.py "people who need help editing UGC ads"

Drafting a query uses Google Gemini, which is free. Previewing a query
asks the X supplier for one page of results, which does cost a small
amount, so previewing is always a separate decision.
"""

from __future__ import annotations

import json
import os
import sys
from typing import Any, Dict, List, Optional

import requests
from dotenv import load_dotenv

load_dotenv()

MODEL_NAME = "gemini-2.5-flash"
SEARCH_URL = "https://api.twitterapi.io/twitter/tweet/advanced_search"


class QueryBuilderError(Exception):
    """Raised with a message a non-technical person can act on."""


# ---------------------------------------------------------------------
# The instructions given to the model
# ---------------------------------------------------------------------

SYSTEM_INSTRUCTION = """
You turn a plain English description of the kind of person somebody
wants to find on X into a working X advanced search query.

You return one JSON object and nothing else, in this exact shape:

{
  "name": "two to four words, a label a human will recognise",
  "description": "one or two plain sentences describing who counts as a
                  match, written to be read later by a scoring model",
  "query": "the X advanced search query",
  "reasoning": "one short sentence on the choice you made"
}

HOW TO BUILD THE QUERY

Use exactly two required groups, separated by a single space, then the
two fixed filters. Nothing else.

  (topic words joined by OR) (intent phrases joined by OR) lang:en -filter:retweets

Group one is the subject. Give between four and eight alternatives
covering the tools, formats, or jargon a real person would type. Put
multi word terms in double quotes.

Group two is the intent. Give between four and eight alternatives that
show somebody wants the work done, such as "looking for", "need
someone", "who can build", "anyone know", "recommend", hiring.

THE RULE THAT MATTERS MOST

Never use more than two required groups. Every extra requirement
multiplies the chance of matching nothing at all. A query that returns
forty loosely relevant posts is far more useful than one that returns
zero perfect ones, because a scoring model reads every result
afterwards and throws the noise away. Bias towards breadth.

For the same reason, prefer short common phrasings over precise
jargon. People post casually. Write the query for how they actually
type, not for how the industry describes itself.

Do not use from:, to:, since:, until:, min_faves, or -filter:replies
unless the person explicitly asked for that restriction.

THE DESCRIPTION

Write it as a sentence about the person, not about the search. It is
read by the scorer, so it should make clear who is a buyer. Say what
they want done and, where useful, who they are.

WORKED EXAMPLES

Input: people looking for someone to build a custom AI workflow
Output:
{
  "name": "Custom AI workflows",
  "description": "People asking for help building an automation, an AI
   agent, or a workflow that connects their existing tools together.",
  "query": "(\\"AI workflow\\" OR \\"AI agent\\" OR n8n OR Zapier OR \\"Make.com\\" OR automation) (\\"looking for\\" OR \\"need someone\\" OR \\"who can build\\" OR \\"anyone know\\" OR hiring) lang:en -filter:retweets",
  "reasoning": "Kept the tool list broad because most people name a
   specific tool rather than the category."
}

Input: brands that want short vertical video ads made for TikTok
Output:
{
  "name": "Vertical video ads",
  "description": "Brands and marketers who want short vertical video
   advertisements produced for TikTok or similar feeds.",
  "query": "(\\"TikTok ads\\" OR \\"vertical video\\" OR \\"short form video\\" OR \\"video ads\\" OR UGC) (\\"looking for\\" OR \\"need someone\\" OR \\"anyone know\\" OR hiring OR \\"who can make\\") lang:en -filter:retweets",
  "reasoning": "Used the ad formats people actually type rather than
   the phrase short vertical video ads, which nobody writes."
}

Return only the JSON object. No markdown, no code fences, no
explanation outside the JSON.
"""


# ---------------------------------------------------------------------
# Talking to Gemini
# ---------------------------------------------------------------------

def build_client(api_key: Optional[str] = None):
    """Create the Gemini client. Separated out so tests can replace it."""
    key = api_key or os.getenv("GEMINI_API_KEY")
    if not key:
        raise QueryBuilderError(
            "No Google key found. Open .env and add the line "
            "GEMINI_API_KEY=your_key_here")

    try:
        from google import genai
    except ImportError:
        raise QueryBuilderError(
            "The google-genai package is not installed. With (venv) "
            "showing in PowerShell, run: pip install google-genai")

    try:
        return genai.Client(api_key=key)
    except Exception as error:
        raise QueryBuilderError(
            "Could not start the AI client. " + str(error)[:200])


def strip_fences(raw: str) -> str:
    """Remove code fences if the model adds them despite instructions."""
    text = (raw or "").strip()
    if text.startswith("```"):
        text = text.split("\n", 1)[-1]
        if text.rstrip().endswith("```"):
            text = text.rstrip()[:-3]
    return text.strip()


def tidy_spaces(value: str) -> str:
    """Collapse the line breaks the model sometimes leaves in a string."""
    return " ".join(str(value or "").split())


def draft_watchlist(wish: str, client=None) -> Dict[str, str]:
    """
    Turn a plain English sentence into a proposed watchlist.

    Returns a dictionary with name, description, query, and reasoning.
    Raises QueryBuilderError with a readable message on failure.
    """
    wish = (wish or "").strip()

    if len(wish) < 8:
        raise QueryBuilderError(
            "Describe who you are looking for in a full sentence, for "
            "example: people who need help editing UGC ads.")

    if len(wish) > 600:
        raise QueryBuilderError(
            "That description is very long. Two or three sentences work "
            "better than a paragraph.")

    if client is None:
        client = build_client()

    try:
        from google.genai import types
        settings = types.GenerateContentConfig(
            system_instruction=SYSTEM_INSTRUCTION,
            response_mime_type="application/json",
            temperature=0.3,
        )
    except ImportError:
        raise QueryBuilderError(
            "The google-genai package is not installed properly.")

    prompt = "Build a watchlist for this request:\n\n" + wish

    try:
        response = client.models.generate_content(
            model=MODEL_NAME, contents=prompt, config=settings)
    except Exception as error:
        message = str(error)
        if "429" in message or "RESOURCE_EXHAUSTED" in message:
            raise QueryBuilderError(
                "The free Google tier is busy. Wait about a minute and "
                "try again.")
        if "401" in message or "API_KEY" in message.upper():
            raise QueryBuilderError(
                "Your Google key was rejected. Check GEMINI_API_KEY "
                "in your .env file.")
        raise QueryBuilderError(
            "The model could not be reached. " + message[:200])

    text = strip_fences(getattr(response, "text", "") or "")
    if not text:
        raise QueryBuilderError("The model replied with nothing. Try again.")

    try:
        result = json.loads(text)
    except ValueError:
        raise QueryBuilderError(
            "The model's reply was not readable. Try rewording your "
            "description and asking again.")

    if not isinstance(result, dict):
        raise QueryBuilderError("The model replied in an unexpected shape.")

    draft = {
        "name": tidy_spaces(result.get("name"))[:60],
        "description": tidy_spaces(result.get("description"))[:400],
        "query": tidy_spaces(result.get("query"))[:800],
        "reasoning": tidy_spaces(result.get("reasoning"))[:300],
    }

    if not draft["name"]:
        draft["name"] = "New watchlist"

    if not draft["query"]:
        raise QueryBuilderError(
            "The model did not produce a search query. Try describing "
            "the people you want in different words.")

    draft["warnings"] = check_query(draft["query"])
    return draft


# ---------------------------------------------------------------------
# Checking a query before anybody spends money on it
# ---------------------------------------------------------------------

def check_query(query: str) -> List[str]:
    """
    Look for the shapes that reliably return nothing.

    This is not clever. It catches the two mistakes that caused an
    empty watchlist in this project already: too many required groups,
    and forgetting the language and retweet filters.
    """
    warnings: List[str] = []
    text = query or ""

    depth = 0
    groups = 0
    for character in text:
        if character == "(":
            if depth == 0:
                groups += 1
            depth += 1
        elif character == ")":
            depth = max(0, depth - 1)

    if depth != 0:
        warnings.append(
            "The brackets do not balance, so the search will be rejected.")

    if groups > 2:
        warnings.append(
            "This has " + str(groups) + " required groups. Anything above "
            "two often matches nothing at all. Consider removing one.")

    if text.count('"') % 2 != 0:
        warnings.append("There is an odd number of quotation marks.")

    if "lang:" not in text:
        warnings.append(
            "No language filter, so you will collect posts you cannot read.")

    if "-filter:retweets" not in text:
        warnings.append(
            "Reposts are not excluded, so you will pay for duplicates.")

    if len(text) > 500:
        warnings.append(
            "This query is very long, which usually means it is too "
            "specific to match anything.")

    return warnings


def preview_query(query: str, api_key: Optional[str] = None,
                  sample_size: int = 5) -> Dict[str, Any]:
    """
    Ask the supplier for one page of results so you can see whether the
    query finds anything before saving it.

    This costs money. One page is roughly twenty posts.
    """
    key = api_key or os.getenv("TWITTER_API_KEY")
    if not key:
        raise QueryBuilderError(
            "No supplier key found. Open .env and add the line "
            "TWITTER_API_KEY=your_key_here")

    if not (query or "").strip():
        raise QueryBuilderError("There is no query to preview.")

    try:
        response = requests.get(
            SEARCH_URL,
            headers={"X-API-Key": key},
            params={"query": query, "queryType": "Latest"},
            timeout=30,
        )
    except requests.exceptions.RequestException as error:
        raise QueryBuilderError(
            "Could not reach the supplier. Check your internet connection. "
            + str(error)[:150])

    if response.status_code in (401, 403):
        raise QueryBuilderError(
            "The supplier rejected your key. Check TWITTER_API_KEY in .env.")

    if response.status_code == 402:
        raise QueryBuilderError(
            "You appear to be out of credits with the supplier.")

    if response.status_code == 429:
        raise QueryBuilderError(
            "Too many requests too quickly. Wait a moment and try again.")

    if response.status_code != 200:
        raise QueryBuilderError(
            "The supplier refused this query. It usually means the search "
            "syntax is wrong. Status " + str(response.status_code))

    try:
        data = response.json()
    except ValueError:
        raise QueryBuilderError("The supplier replied in an unexpected format.")

    tweets = data.get("tweets")
    if not isinstance(tweets, list):
        tweets = []

    samples = []
    for tweet in tweets[:sample_size]:
        author = tweet.get("author") or {}
        samples.append({
            "author": author.get("userName", "unknown"),
            "text": (tweet.get("text") or "")[:280],
            "posted_at": tweet.get("createdAt", ""),
            "post_url": tweet.get("url", ""),
        })

    if tweets:
        verdict = ("This query finds posts. " + str(len(tweets))
                   + " came back on the first page.")
    else:
        verdict = ("This query matched nothing. It is almost certainly too "
                   "narrow. Remove a required phrase and try again.")

    return {
        "received": len(tweets),
        "samples": samples,
        "verdict": verdict,
        "has_more": bool(data.get("has_next_page")),
    }


# ---------------------------------------------------------------------
# Running it from PowerShell on its own
# ---------------------------------------------------------------------

def main() -> int:
    if len(sys.argv) < 2:
        print("Describe who you are looking for, in quotes. For example:")
        print("")
        print('    python query_builder.py "people who need UGC ads edited"')
        return 1

    wish = " ".join(sys.argv[1:])

    try:
        draft = draft_watchlist(wish)
    except QueryBuilderError as error:
        print(error)
        return 1

    print("")
    print("=" * 62)
    print("PROPOSED WATCHLIST")
    print("=" * 62)
    print("  Name        :", draft["name"])
    print("  Description :", draft["description"])
    print("")
    print("  Query       :")
    print("   ", draft["query"])
    print("")
    print("  Why         :", draft["reasoning"])

    if draft["warnings"]:
        print("")
        print("  Worth checking:")
        for warning in draft["warnings"]:
            print("   -", warning)

    print("=" * 62)
    print("")
    print("Nothing was saved. Open the app and use the Describe tab to")
    print("preview this against real posts and add it to your watchlists.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
