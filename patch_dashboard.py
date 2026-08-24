"""
A one time upgrade for dashboard_app.html.

What it adds:

score.py now makes the model quote the exact words from a post that
prove somebody wants to pay for work, and then checks that quote really
appears in the post. Every lead carries three new fields:

    evidence           the quoted words
    evidence_verified  whether they were found in the post
    score_capped       whether the score was held back for lack of proof

app.py already sends all three to the browser. The dashboard never
displays them, so the most persuasive thing the tool produces is
invisible. This puts the quote on the card, directly under the reason,
and adds a warning chip to any lead whose score was held back.

Why it matters: a reason is the model's opinion. A quote is the
author's own words. A reviewer can trust a score in one glance instead
of opening the post to check.

This script is careful:
  - it saves a backup at dashboard_app.html.backup first
  - it refuses to run twice
  - it makes all three edits or none of them
  - if the result looks wrong, it restores your original

Run it once, from C:\\x-lead-finder:

    python patch_dashboard.py
"""

import os
import shutil

TARGET = "dashboard_app.html"
BACKUP = "dashboard_app.html.backup"

# If this is already in the file, the patch has been applied before.
MARKER = "their words, found in the post"


# --- edit one, the styling -------------------------------------------

CSS_ANCHOR = """.chips { display: flex; gap: 6px; flex-wrap: wrap; margin-bottom: 10px; }"""

CSS_NEW = """/* The author's own words. Set in the mono face because a quote
   checked against the source is data, not prose. */
.quote {
  font-family: var(--mono);
  font-size: 12.5px;
  line-height: 1.45;
  background: #F2F5F8;
  border-left: 2px solid var(--signal);
  padding: 8px 10px;
  margin-bottom: 10px;
  word-break: break-word;
}
.quote.unproven { border-left-color: var(--alert); background: #FAF3F4; }
.quote em {
  display: block;
  font-family: var(--body);
  font-style: normal;
  font-size: 10px;
  letter-spacing: 0.09em;
  text-transform: uppercase;
  color: var(--ink-faint);
  margin-bottom: 3px;
}
.quote.unproven em { color: var(--alert); }

.chip.capped { border-color: var(--alert); color: var(--alert); }

.chips { display: flex; gap: 6px; flex-wrap: wrap; margin-bottom: 10px; }"""


# --- edit two, showing the quote --------------------------------------

QUOTE_ANCHOR = """  if (lead.reason) body.appendChild(make("div", "reason", lead.reason));"""

QUOTE_NEW = """  if (lead.reason) body.appendChild(make("div", "reason", lead.reason));

  /* The author's own words, checked against the post text by score.py.
     A reason is the model's opinion. A quote is evidence. This is what
     lets a reviewer trust a score without opening the post, so it sits
     directly beneath the reason.

     textContent again, never innerHTML, because this string came from
     a stranger on the internet. */
  if (lead.evidence) {
    const proven = lead.evidence_verified !== false;
    const quote = make("div", "quote" + (proven ? "" : " unproven"));
    quote.appendChild(make("em", null,
      proven ? "their words, found in the post"
             : "the model quoted this, but it is not in the post"));
    quote.appendChild(document.createTextNode(lead.evidence));
    body.appendChild(quote);
  }"""


# --- edit three, the warning chip -------------------------------------

CHIP_ANCHOR = """  if (lead.budget_signal) {
    chips.appendChild(make("span", "chip money", "money mentioned"));
  }"""

CHIP_NEW = """  if (lead.budget_signal) {
    chips.appendChild(make("span", "chip money", "money mentioned"));
  }
  if (lead.score_capped) {
    chips.appendChild(make("span", "chip capped", "score held back"));
  }"""


EDITS = [
    ("the styling", CSS_ANCHOR, CSS_NEW),
    ("the evidence quote", QUOTE_ANCHOR, QUOTE_NEW),
    ("the warning chip", CHIP_ANCHOR, CHIP_NEW),
]


def stop(message):
    print(message)
    raise SystemExit(1)


def main():
    if not os.path.exists(TARGET):
        stop("Could not find dashboard_app.html in this folder.\n"
             "Run this from C:\\x-lead-finder, where the file lives.")

    with open(TARGET, "r", encoding="utf-8") as handle:
        original = handle.read()

    if MARKER in original:
        print("dashboard_app.html has already been upgraded. "
              "Nothing was changed.")
        return

    # Check every anchor before touching anything, so we never leave the
    # file half edited.
    for label, anchor, _ in EDITS:
        found = original.count(anchor)
        if found == 0:
            stop("The part of the file that holds " + label + " does not\n"
                 "look the way this script expects, so nothing was changed.\n"
                 "Your file is untouched. Send me dashboard_app.html and I\n"
                 "will adjust the upgrade.")
        if found > 1:
            stop("The part of the file that holds " + label + " appears "
                 + str(found) + " times,\nwhich is not what I expected. "
                 "Nothing was changed.")

    shutil.copyfile(TARGET, BACKUP)
    print("Backup saved as", BACKUP)

    updated = original
    for _, anchor, replacement in EDITS:
        updated = updated.replace(anchor, replacement, 1)

    with open(TARGET, "w", encoding="utf-8") as handle:
        handle.write(updated)

    # Confirm all three edits actually landed, and that nothing was lost.
    problems = []
    if MARKER not in updated:
        problems.append("the evidence quote did not go in")
    if ".chip.capped" not in updated:
        problems.append("the styling did not go in")
    if "score held back" not in updated:
        problems.append("the warning chip did not go in")
    if len(updated) <= len(original):
        problems.append("the file did not grow, so something was removed")

    if problems:
        shutil.copyfile(BACKUP, TARGET)
        stop("The upgrade did not come out right, so your original file was\n"
             "restored. Nothing is broken.\nProblems: " + "; ".join(problems))

    print("")
    print("Done. Three changes, all inside the lead card:")
    print("  1. Styling for the quote and the warning chip.")
    print("  2. The quoted evidence, shown under the reason.")
    print("  3. A held back chip on any lead without proof.")
    print("")
    print("Restart the app to see it:")
    print("  press Ctrl and C if it is running, then  python app.py")
    print("")
    print("Your original is in", BACKUP, "if you want to compare.")


if __name__ == "__main__":
    main()
