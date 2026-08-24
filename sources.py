"""
The data source adapter.

Purpose: let the client choose where posts come from, without changing
a line of code anywhere else.

Two sources ship with the tool:

  twitterapi    a third party supplier, cheap, free starter credits,
                but it scrapes rather than licenses, which sits against
                X's terms of service
  official_x    X's own API, fully compliant, roughly thirty times the
                price and with no free tier for new developers

Switch between them with one line in config.yaml:

    data_source: twitterapi
    data_source: official_x

Why this file exists: an organisation that cannot use scraped data must
not have to rewrite the tool to move. Everything that differs between
the two lives here, and collect.py never learns there is more than one.

The two APIs differ in four ways, and all four are handled here:

  1. Authentication. One uses an X-API-Key header, the other a Bearer
     token.
  2. Query dialect. X's own API writes -is:retweet where the supplier
     writes -filter:retweets.
  3. Time limits. The supplier accepts since_time inside the query. X's
     API takes a start_time parameter instead, and refuses to look back
     further than seven days.
  4. Reply shape. X returns authors in a separate block that has to be
     stitched back onto the posts.

Each source returns posts in the same shape, so collect.py's tidy()
function works unchanged either way. The adapter's whole job is to make
the official API look like the one already supported.
"""

import time
from datetime import datetime, timedelta, timezone

import requests

# Wait these many seconds when asked to slow down, then try again.
RETRY_WAITS = (5, 15, 30)

# X's recent search will not look back further than seven days. Asking
# for more is an error rather than a smaller result, so clamp it.
OFFICIAL_MAX_LOOKBACK_DAYS = 7


class SourceError(Exception):
    """Something the run cannot continue past, worded for a human."""


# ---------------------------------------------------------------------
# Shared request handling
# ---------------------------------------------------------------------

def request_with_patience(url, headers, params, delay, retries, label):
    """
    Make one request, politely, retrying if asked to slow down.

    Returns the parsed JSON, or None if this watchlist should be
    abandoned. Raises SourceError for problems no retry can fix, such
    as a rejected key or exhausted credits.
    """
    attempts = max(1, retries + 1)

    for attempt in range(attempts):
        if delay > 0:
            time.sleep(delay)

        try:
            response = requests.get(url, headers=headers,
                                    params=params, timeout=30)
        except requests.exceptions.RequestException as error:
            print("  Could not reach %s. Skipping this watchlist." % label)
            print("  Details:", error)
            return None

        if response.status_code in (401, 403):
            raise SourceError(
                "%s rejected your key. Open .env and check it is correct."
                % label)
        if response.status_code in (402, payment_required()):
            raise SourceError(
                "%s says you are out of credits. Top up or wait." % label)

        if response.status_code == 429:
            if attempt < attempts - 1:
                wait = RETRY_WAITS[min(attempt, len(RETRY_WAITS) - 1)]
                print("  Asked to slow down. Waiting %d seconds, then "
                      "trying again." % wait)
                time.sleep(wait)
                continue
            print("  Still being asked to slow down after %d tries. "
                  "Skipping this watchlist for now." % attempts)
            return None

        if response.status_code != 200:
            print("  %s refused the request. Status: %d"
                  % (label, response.status_code))
            print("  Message:", response.text[:300])
            return None

        try:
            return response.json()
        except ValueError:
            print("  %s replied in an unexpected format. Skipping." % label)
            return None

    return None


def payment_required():
    """X uses 402 as well, but keeping this named makes the check read."""
    return 402


# ---------------------------------------------------------------------
# Source one, the third party supplier
# ---------------------------------------------------------------------

class TwitterApiSource:
    """
    twitterapi.io. Accepts the same advanced search syntax as x.com,
    including since_time written directly into the query.
    """

    name = "twitterapi"
    label = "twitterapi.io"
    key_name = "TWITTER_API_KEY"
    default_price_per_1000 = 0.15
    compliance_note = ("A third party that scrapes rather than licenses. "
                       "Cheap, but against X's terms of service.")

    SEARCH_URL = "https://api.twitterapi.io/twitter/tweet/advanced_search"

    def __init__(self, api_key):
        if not api_key:
            raise SourceError(
                "No supplier key found. Check that .env exists in this "
                "folder and contains TWITTER_API_KEY=your_key")
        self.api_key = api_key

    def build_query(self, query, since_time):
        """This supplier takes the time limit inside the query itself."""
        if since_time is None:
            return query
        if "since_time:" in query or "since:" in query:
            return query
        return "%s since_time:%d" % (query, since_time)

    def fetch_page(self, query, cursor, since_time, delay, retries):
        params = {"query": self.build_query(query, since_time),
                  "queryType": "Latest"}
        if cursor:
            params["cursor"] = cursor

        data = request_with_patience(
            self.SEARCH_URL, {"X-API-Key": self.api_key}, params,
            delay, retries, self.label)

        if data is None:
            return None, ""

        tweets = data.get("tweets")
        if not isinstance(tweets, list):
            tweets = []

        next_cursor = data.get("next_cursor") or ""
        if not data.get("has_next_page", False):
            next_cursor = ""

        return tweets, next_cursor


# ---------------------------------------------------------------------
# Source two, X's own API
# ---------------------------------------------------------------------

class OfficialXSource:
    """
    X API v2 recent search.

    Compliant, licensed, and about thirty times the price. Reads cost
    roughly 0.005 dollars each, so 1000 posts is about 5 dollars rather
    than 15 cents.
    """

    name = "official_x"
    label = "the X API"
    key_name = "X_BEARER_TOKEN"
    default_price_per_1000 = 5.00
    compliance_note = ("X's own licensed API. Fully compliant, no free "
                       "tier for new developers, about thirty times the "
                       "price.")

    SEARCH_URL = "https://api.x.com/2/tweets/search/recent"

    # What X writes, against what the supplier writes.
    DIALECT = (
        ("-filter:retweets", "-is:retweet"),
        ("-filter:replies", "-is:reply"),
        ("filter:retweets", "is:retweet"),
        ("filter:replies", "is:reply"),
        ("-filter:links", "-has:links"),
        ("filter:links", "has:links"),
    )

    def __init__(self, api_key):
        if not api_key:
            raise SourceError(
                "No X bearer token found. Open .env and add the line\n"
                "X_BEARER_TOKEN=your_token_here\n"
                "Get one from the developer portal at developer.x.com")
        self.api_key = api_key

    def build_query(self, query, since_time):
        """
        Translate the query into X's dialect and strip anything it takes
        as a parameter instead.
        """
        translated = query
        for theirs, ours in self.DIALECT:
            translated = translated.replace(theirs, ours)

        # since_time is a parameter here, not a query operator. Remove
        # any that survived from a query written for the other supplier.
        parts = [word for word in translated.split()
                 if not word.startswith("since_time:")
                 and not word.startswith("until_time:")]
        return " ".join(parts).strip()

    def start_time_for(self, since_time):
        """
        Turn a unix timestamp into the RFC 3339 string X expects, never
        reaching back further than X allows.
        """
        if since_time is None:
            return None

        earliest = datetime.now(timezone.utc) - timedelta(
            days=OFFICIAL_MAX_LOOKBACK_DAYS, seconds=-120)
        wanted = datetime.fromtimestamp(since_time, timezone.utc)
        chosen = max(wanted, earliest)
        return chosen.strftime("%Y-%m-%dT%H:%M:%SZ")

    def fetch_page(self, query, cursor, since_time, delay, retries):
        params = {
            "query": self.build_query(query, since_time),
            "max_results": 100,
            "tweet.fields": "created_at,public_metrics,author_id",
            "expansions": "author_id",
            "user.fields": "username,name,public_metrics",
        }
        start_time = self.start_time_for(since_time)
        if start_time:
            params["start_time"] = start_time
        if cursor:
            params["next_token"] = cursor

        data = request_with_patience(
            self.SEARCH_URL, {"Authorization": "Bearer " + self.api_key},
            params, delay, retries, self.label)

        if data is None:
            return None, ""

        posts = data.get("data")
        if not isinstance(posts, list):
            posts = []

        people = {}
        includes = data.get("includes") or {}
        for person in includes.get("users") or []:
            if isinstance(person, dict) and person.get("id"):
                people[str(person["id"])] = person

        tweets = [self.reshape(post, people) for post in posts
                  if isinstance(post, dict)]

        meta = data.get("meta") or {}
        return tweets, meta.get("next_token") or ""

    def reshape(self, post, people):
        """
        Rebuild one post in the shape the rest of the tool already
        understands, so nothing downstream needs to know the difference.
        """
        person = people.get(str(post.get("author_id", ""))) or {}
        username = person.get("username", "")
        counts = post.get("public_metrics") or {}
        follower_counts = person.get("public_metrics") or {}
        post_id = str(post.get("id", ""))

        return {
            "id": post_id,
            "author": {
                "userName": username,
                "name": person.get("name", ""),
                "followers": follower_counts.get("followers_count", 0),
            },
            "text": post.get("text", ""),
            "url": ("https://x.com/%s/status/%s" % (username, post_id)
                    if username and post_id else ""),
            "createdAt": post.get("created_at", ""),
            "likeCount": counts.get("like_count", 0),
            "replyCount": counts.get("reply_count", 0),
        }


# ---------------------------------------------------------------------
# Choosing one
# ---------------------------------------------------------------------

AVAILABLE = {
    TwitterApiSource.name: TwitterApiSource,
    OfficialXSource.name: OfficialXSource,
}

DEFAULT_SOURCE = TwitterApiSource.name


def key_name_for(chosen):
    """Which .env line a given source needs. Used before a key is read."""
    source = AVAILABLE.get(str(chosen or "").strip().lower())
    return (source or AVAILABLE[DEFAULT_SOURCE]).key_name


def build(chosen, api_key):
    """
    Return the source named in config.yaml.

    An unknown name is a settings mistake, not a crash, so it is
    reported in plain words with the valid options listed.
    """
    wanted = str(chosen or DEFAULT_SOURCE).strip().lower()
    source = AVAILABLE.get(wanted)
    if source is None:
        raise SourceError(
            "config.yaml asks for a data source called '%s', which does "
            "not exist.\nChoose one of: %s"
            % (wanted, ", ".join(sorted(AVAILABLE))))
    return source(api_key)


if __name__ == "__main__":
    print("=" * 62)
    print("DATA SOURCES AVAILABLE")
    print("=" * 62)
    for key in sorted(AVAILABLE):
        source = AVAILABLE[key]
        print("")
        print("  data_source: " + key)
        print("    Name in .env  :", source.key_name)
        print("    Price per 1000: about $%.2f" % source.default_price_per_1000)
        print("    Note          :", source.compliance_note)
    print("")
    print("=" * 62)
    print("Set the one you want in config.yaml, on a line of its own.")
