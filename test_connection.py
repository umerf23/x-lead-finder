"""
Step 3 connection test.

Purpose: confirm that your computer can reach the X data supplier
and pull back real public posts.

Before running this, make sure a file named .env sits in the same
folder, containing one line:

    TWITTER_API_KEY=your_real_key_here

Run it with:

    python test_connection.py
"""

import os
import requests
from dotenv import load_dotenv

load_dotenv()

API_KEY = os.getenv("TWITTER_API_KEY")

if not API_KEY:
    print("No API key found.")
    print("Check that a file named .env sits in this same folder,")
    print("and that it contains the line TWITTER_API_KEY=your_key")
    raise SystemExit(1)

URL = "https://api.twitterapi.io/twitter/tweet/advanced_search"

params = {
    "query": '"looking for someone" AI lang:en -filter:retweets',
    "queryType": "Latest",
}

headers = {"X-API-Key": API_KEY}

print("Asking X for posts. Please wait.")

try:
    response = requests.get(URL, headers=headers, params=params, timeout=30)
except requests.exceptions.RequestException as error:
    print("Could not reach the server. Check your internet connection.")
    print("Details:", error)
    raise SystemExit(1)

if response.status_code == 401 or response.status_code == 403:
    print("The server rejected your key. Status code:", response.status_code)
    print("Open your .env file and check the key is correct,")
    print("with no spaces, no quotes, and nothing after it.")
    raise SystemExit(1)

if response.status_code != 200:
    print("The server refused the request. Status code:", response.status_code)
    print("Message:", response.text[:500])
    raise SystemExit(1)

try:
    data = response.json()
except ValueError:
    print("The server replied, but not in the format expected.")
    print("Raw reply:", response.text[:500])
    raise SystemExit(1)

tweets = data.get("tweets", [])

if not tweets:
    print("Connected successfully, but no posts matched right now.")
    print("That is still a pass. Your connection works.")
    raise SystemExit(0)

print("Success. Here are the first five matching posts.")
print("-" * 60)

for tweet in tweets[:5]:
    author = tweet.get("author") or {}
    name = author.get("userName", "unknown")
    text = tweet.get("text", "")
    created = tweet.get("createdAt", "unknown time")
    link = tweet.get("url", "")

    print("Author   : @" + name)
    print("Posted   :", created)
    print("Post     :", text[:200].replace("\n", " "))
    print("Link     :", link)
    print("-" * 60)

print("Total posts received:", len(tweets))
