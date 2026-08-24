"""
A one time repair for app.py.

The problem it fixes:

In app.py, save_settings builds a brand new dictionary out of seven
named keys and writes that over config.yaml. Every other setting in
the file is thrown away. That means the first time you press Save on
the settings page, these disappear without warning:

  watcher:                  the whole polling and digest block
  only_new_posts            the switch that stops you buying duplicates
  since_overlap_minutes     the safety window on that switch
  request_delay_seconds     the pause that keeps you under the rate limit
  rate_limit_retries        how many times to wait and try again

The fix is two lines: read the existing config first, then change only
the keys the settings page actually owns. save_watchlist in the same
file already does it this way, so this brings the two into line.

This script is careful:
  - it saves a backup at app.py.backup before touching anything
  - it refuses to run twice
  - it checks the result is still valid Python before finishing
  - if anything is wrong, it restores your original and stops

Run it once, from C:\\x-lead-finder:

    python patch_app.py
"""

import ast
import os
import shutil

TARGET = "app.py"
BACKUP = "app.py.backup"

OLD_BLOCK = '''    config = {
        "max_posts_per_run": max(1, settings.max_posts_per_run),
        "max_posts_per_watchlist": max(1, settings.max_posts_per_watchlist),
        "max_posts_per_author": max(1, settings.max_posts_per_author),
        "max_pages_per_watchlist": max(1, settings.max_pages_per_watchlist),
        "cost_per_1000_posts": max(0.0, settings.cost_per_1000_posts),
        "confirm_before_spending": bool(settings.confirm_before_spending),
        "watchlists": cleaned,
    }
'''

NEW_BLOCK = '''    # Read the file first, then change only the keys this page owns.
    # Building a fresh dictionary here would silently delete every
    # other setting, including the watcher block used by watch.py and
    # the only_new_posts switch that stops collect.py buying the same
    # posts twice. A settings page must never eat settings it does
    # not display.
    config = read_config()
    config.update({
        "max_posts_per_run": max(1, settings.max_posts_per_run),
        "max_posts_per_watchlist": max(1, settings.max_posts_per_watchlist),
        "max_posts_per_author": max(1, settings.max_posts_per_author),
        "max_pages_per_watchlist": max(1, settings.max_pages_per_watchlist),
        "cost_per_1000_posts": max(0.0, settings.cost_per_1000_posts),
        "confirm_before_spending": bool(settings.confirm_before_spending),
        "watchlists": cleaned,
    })
'''

MARKER = "config = read_config()\n    config.update({"


def stop(message):
    print(message)
    raise SystemExit(1)


def main():
    if not os.path.exists(TARGET):
        stop("Could not find app.py in this folder.\n"
             "Run this from C:\\x-lead-finder, the same place app.py lives.")

    with open(TARGET, "r", encoding="utf-8") as handle:
        original = handle.read()

    if MARKER in original:
        print("app.py has already been repaired. Nothing was changed.")
        return

    if OLD_BLOCK not in original:
        stop("The part of app.py that needed fixing does not look the way\n"
             "this script expects, so nothing was changed. Your file is\n"
             "untouched. Send me your app.py and I will adjust the repair.")

    if original.count(OLD_BLOCK) > 1:
        stop("That block appears more than once in app.py, which is not\n"
             "what I expected. Nothing was changed. Send me the file.")

    shutil.copyfile(TARGET, BACKUP)
    print("Backup saved as", BACKUP)

    repaired = original.replace(OLD_BLOCK, NEW_BLOCK)

    with open(TARGET, "w", encoding="utf-8") as handle:
        handle.write(repaired)

    # Prove the file is still valid Python. If it is not, undo it.
    try:
        with open(TARGET, "r", encoding="utf-8") as handle:
            ast.parse(handle.read(), filename=TARGET)
    except SyntaxError as error:
        shutil.copyfile(BACKUP, TARGET)
        stop("The repaired file did not parse, so your original app.py was\n"
             "restored. Nothing is broken.\nDetails: " + str(error))

    print("")
    print("Done. The settings page now keeps settings it does not display.")
    print("")
    print("Changed: one block inside save_settings, near the end of the")
    print("function, so that it reads config.yaml before writing it back.")
    print("")
    print("Nothing else in app.py was touched. Your original is in")
    print(BACKUP, "if you want to compare them.")


if __name__ == "__main__":
    main()
