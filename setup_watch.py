"""
Step 11 setup helper.

Purpose: add the watcher settings to config.yaml without you having to
open the file in Notepad, which mangles the spacing.

It is careful:
- it makes a backup first, at config.backup.yaml
- it refuses to run twice
- it checks the result still reads as valid YAML
- if anything is wrong, it puts the original file back

Run it once:
    python setup_watch.py
"""

import os
import re
import shutil

import yaml

CONFIG_FILE = "config.yaml"
BACKUP_FILE = "config.backup.yaml"

BLOCK = """
# ---------------------------------------------------------------------
# Watcher settings, added in Step 11.
# These control the unattended run and the daily digest.
# Change the numbers here, never in the code.
# ---------------------------------------------------------------------
watcher:

  # How often a collection cycle runs, in minutes.
  poll_every_minutes: 10

  # The hard daily limit on posts received from the supplier. This is
  # the number you pay for. At 0.15 dollars per 1000 posts, 200 posts
  # is about 3 cents a day.
  daily_received_cap: 200

  # Rebuild the static dashboard.html after each cycle.
  rebuild_dashboard: true

  # The once a day summary of the best new leads.
  digest_enabled: true

  # Local 24 hour time, as hours colon minutes.
  digest_at: "18:00"

  # Only leads scoring this or higher go in the digest.
  digest_min_score: 65

  # How many leads the digest carries at most.
  digest_top_n: 10
"""


def stop(message):
    print(message)
    raise SystemExit(1)


def main():
    if not os.path.exists(CONFIG_FILE):
        stop("Could not find config.yaml in this folder. "
             "Run this from C:\\x-lead-finder")

    with open(CONFIG_FILE, "r", encoding="utf-8") as handle:
        original = handle.read()

    if re.search(r"^watcher\s*:", original, flags=re.MULTILINE):
        print("config.yaml already has a watcher section. Nothing was changed.")
        print("Open it in a proper editor if you want to adjust the numbers.")
        return

    shutil.copyfile(CONFIG_FILE, BACKUP_FILE)
    print("Backup saved as", BACKUP_FILE)

    updated = original.rstrip("\n") + "\n" + BLOCK

    with open(CONFIG_FILE, "w", encoding="utf-8") as handle:
        handle.write(updated)

    # Prove the file still parses. If it does not, undo the change.
    try:
        with open(CONFIG_FILE, "r", encoding="utf-8") as handle:
            parsed = yaml.safe_load(handle)
        if not isinstance(parsed, dict) or "watcher" not in parsed:
            raise yaml.YAMLError("the watcher section did not come through")
    except yaml.YAMLError as error:
        shutil.copyfile(BACKUP_FILE, CONFIG_FILE)
        stop("The edit did not read back correctly, so your original "
             "config.yaml was restored.\nDetails: " + str(error))

    print("Done. config.yaml now has a watcher section with these settings:")
    print("  poll_every_minutes  10")
    print("  daily_received_cap  200 posts, about 3 cents a day")
    print("  digest_at           18:00")
    print("  digest_min_score    65")
    print("")
    print("Nothing else in the file was touched.")


if __name__ == "__main__":
    main()
