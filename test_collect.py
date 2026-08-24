"""
Tests for the spend ledger in collect.py.

Purpose: prove the daily cap counts every post that was actually paid
for, including the posts bought before a run failed.

The bug these guard against was real. When a later watchlist raised a
SourceError, for example when credits ran out part way through a run,
collect.py exited before it reached the line that wrote to the ledger.
The posts already received had been paid for, and the ledger recorded
nothing. Repeat that a few times and the daily cap is measuring a
fraction of your spending, which is the exact failure spend.py was
written to prevent.

No network, no API keys, no cost. A fake supplier stands in for the
real one, and every run happens inside a throwaway folder.

Run them with (venv) showing:

    python test_collect.py
"""

import io
import json
import os
import shutil
import sys
import tempfile
import unittest
from contextlib import redirect_stdout

import collect
import sources
import spend


class FakeSource:
    """
    A supplier that hands back one page of posts per call.

    Set fail_on to the call number that should raise instead, which is
    how a real supplier reports exhausted credits or a rejected key.
    """

    name = "twitterapi"
    label = "fake supplier"
    default_price_per_1000 = 0.15

    def __init__(self, posts_per_page=20, fail_on=None):
        self.posts_per_page = posts_per_page
        self.fail_on = fail_on
        self.calls = 0

    def build_query(self, query, since_time):
        return query

    def fetch_page(self, query, cursor, since_time, delay, retries):
        self.calls += 1
        if self.fail_on is not None and self.calls >= self.fail_on:
            raise sources.SourceError(
                "fake supplier says you are out of credits.")
        posts = [{
            "id": "%d%03d" % (self.calls, n),
            "author": {"userName": "user_%d_%d" % (self.calls, n),
                       "name": "User"},
            "text": "looking for someone to build an AI workflow",
            "url": "https://x.com/u/status/%d%03d" % (self.calls, n),
            "createdAt": "Mon Aug 24 09:00:00 +0000 2026",
        } for n in range(self.posts_per_page)]
        return posts, ""


def config_with(watchlist_names):
    return {
        "data_source": "twitterapi",
        "daily_post_cap": 500,
        "cost_per_1000_posts": 0.15,
        "max_posts_per_run": 500,
        "max_posts_per_watchlist": 20,
        "max_posts_per_author": 5,
        "max_pages_per_watchlist": 1,
        "confirm_before_spending": False,
        "only_new_posts": False,
        "watchlists": [{"name": name, "query": "a OR b", "enabled": True}
                       for name in watchlist_names],
    }


class LedgerTestCase(unittest.TestCase):
    """Each test runs collect.py inside its own empty folder."""

    def setUp(self):
        self.original_dir = os.getcwd()
        self.original_build = sources.build
        self.original_load = collect.load_config
        self.original_argv = sys.argv

        self.folder = tempfile.mkdtemp(prefix="collect-test-")
        os.chdir(self.folder)
        os.makedirs("data", exist_ok=True)
        sys.argv = ["collect.py"]

    def tearDown(self):
        sources.build = self.original_build
        collect.load_config = self.original_load
        sys.argv = self.original_argv
        os.chdir(self.original_dir)
        shutil.rmtree(self.folder, ignore_errors=True)

    def run_collection(self, source, config):
        """Run collect.main() quietly, returning its exit code or None."""
        sources.build = lambda chosen, key: source
        collect.load_config = lambda: config
        try:
            with redirect_stdout(io.StringIO()):
                collect.main()
        except SystemExit as leaving:
            return leaving.code
        return None

    def ledger(self):
        if not os.path.exists("data/spend.json"):
            return None
        with open("data/spend.json", encoding="utf-8") as handle:
            return json.load(handle)


class TestSuccessfulRun(LedgerTestCase):

    def test_every_post_received_is_recorded(self):
        source = FakeSource(posts_per_page=20)
        code = self.run_collection(source, config_with(["First", "Second"]))

        self.assertIsNone(code)
        self.assertEqual(spend.used_today(), 40)

    def test_ledger_file_is_written(self):
        self.run_collection(FakeSource(), config_with(["Only"]))

        ledger = self.ledger()
        self.assertIsNotNone(ledger)
        self.assertEqual(ledger["received_today"], 20)
        self.assertEqual(len(ledger["runs_today"]), 1)


class TestFailedRun(LedgerTestCase):
    """The path the bug lived on."""

    def test_posts_bought_before_the_failure_are_still_recorded(self):
        # First watchlist succeeds and costs money. Second one fails.
        source = FakeSource(posts_per_page=20, fail_on=2)
        code = self.run_collection(source, config_with(["First", "Second"]))

        # The run still fails loudly. That part was always correct.
        self.assertEqual(code, 1)

        # And the twenty posts already paid for are counted.
        self.assertEqual(spend.used_today(), 20)

    def test_failure_on_the_very_first_call_records_nothing(self):
        source = FakeSource(fail_on=1)
        code = self.run_collection(source, config_with(["First"]))

        self.assertEqual(code, 1)
        self.assertEqual(spend.used_today(), 0)

    def test_collected_posts_survive_the_failure(self):
        # Losing the posts you paid for would be its own bug.
        source = FakeSource(posts_per_page=20, fail_on=2)
        self.run_collection(source, config_with(["First", "Second"]))

        with open("data/posts.json", encoding="utf-8") as handle:
            saved = json.load(handle)
        self.assertEqual(len(saved), 20)

    def test_a_failed_run_still_counts_against_the_cap(self):
        # Two failing runs must not buy 40 posts against a cap of 25.
        config = config_with(["First", "Second"])
        config["daily_post_cap"] = 25

        self.run_collection(FakeSource(posts_per_page=20, fail_on=2), config)
        self.assertEqual(spend.remaining(25), 5)

        self.run_collection(FakeSource(posts_per_page=20, fail_on=2), config)
        self.assertEqual(spend.remaining(25), 0)


class TestLedgerArithmetic(unittest.TestCase):
    """spend.py on its own, with no collection involved."""

    def setUp(self):
        self.original_dir = os.getcwd()
        self.folder = tempfile.mkdtemp(prefix="ledger-test-")
        os.chdir(self.folder)
        os.makedirs("data", exist_ok=True)

    def tearDown(self):
        os.chdir(self.original_dir)
        shutil.rmtree(self.folder, ignore_errors=True)

    def test_remaining_never_goes_below_zero(self):
        spend.record(300)
        self.assertEqual(spend.remaining(200), 0)

    def test_runs_accumulate_across_the_day(self):
        spend.record(20, source="watcher")
        spend.record(30, source="web app")
        self.assertEqual(spend.used_today(), 50)
        self.assertEqual(spend.remaining(200), 150)

    def test_a_different_day_resets_the_count(self):
        spend.record(60)
        ledger = spend.load()
        ledger["day"] = "2000-01-01"
        spend.save(ledger)

        self.assertEqual(spend.used_today(), 0)

    def test_the_month_total_survives_the_daily_reset(self):
        spend.record(60)
        ledger = spend.load()
        ledger["day"] = "2000-01-01"
        spend.save(ledger)
        spend.load()

        figures = spend.summary(200, 0.15)
        self.assertEqual(figures["used_today"], 0)
        self.assertEqual(figures["used_this_month"], 60)


if __name__ == "__main__":
    unittest.main(verbosity=2)
