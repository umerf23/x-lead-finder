"""
A one time upgrade for dashboard_app.html, part two.

The problem it fixes:

Real job adverts on X run to several hundred words. The dashboard
prints every one in full, so a single lead can fill the whole window.
The score, the reason and the quoted evidence for the next lead are
pushed off the bottom of the screen, and a review list stops being a
list at all.

The fix: clip any post longer than about three lines, and put a small
Show the whole post link underneath. Nothing is hidden permanently and
one click opens it. Short posts are untouched.

Why it matters: the point of a review dashboard is comparison. Five
leads you can see beats one lead you can read.

This script is careful:
  - it saves a backup at dashboard_app.html.backup2 first
  - it refuses to run twice
  - it makes both edits or neither
  - if the result looks wrong, it restores your original

Run it once, from C:\\x-lead-finder:

    python patch_dashboard_clip.py
"""

import os
import shutil

TARGET = "dashboard_app.html"
BACKUP = "dashboard_app.html.backup2"

MARKER = "Show the whole post"


# --- edit one, the styling -------------------------------------------

CSS_ANCHOR = """.post-text { margin: 0 0 8px; white-space: pre-wrap; word-break: break-word; }"""

CSS_NEW = """.post-text { margin: 0 0 8px; white-space: pre-wrap; word-break: break-word; }

/* A long advert should not swallow the screen. Clip it and fade the
   last line, so it reads as trimmed rather than broken. */
.post-text.clipped {
  max-height: 4.6em;
  overflow: hidden;
  -webkit-mask-image: linear-gradient(180deg, #000 62%, transparent 100%);
          mask-image: linear-gradient(180deg, #000 62%, transparent 100%);
}
.more {
  background: none;
  border: none;
  padding: 0;
  margin: 0 0 10px;
  color: var(--signal);
  font-size: 13px;
  cursor: pointer;
  text-decoration: underline;
  text-underline-offset: 2px;
}
.more:hover { color: #0C5F5B; }
.more:focus-visible { outline: 2px solid var(--signal); outline-offset: 2px; }"""


# --- edit two, the clipping itself ------------------------------------

TEXT_ANCHOR = """  body.appendChild(make("p", "post-text", lead.text || ""));"""

TEXT_NEW = """  const postText = make("p", "post-text", lead.text || "");
  body.appendChild(postText);

  /* Real job adverts run long. Clipping them keeps the score, the
     reason and the evidence for the next lead on screen, which is the
     whole point of a review list. One click opens the full post. */
  if ((lead.text || "").length > 260) {
    postText.classList.add("clipped");
    const more = make("button", "more", "Show the whole post");
    more.addEventListener("click", function () {
      const nowClipped = postText.classList.toggle("clipped");
      more.textContent = nowClipped ? "Show the whole post" : "Show less";
    });
    body.appendChild(more);
  }"""


EDITS = [
    ("the styling", CSS_ANCHOR, CSS_NEW),
    ("the post text", TEXT_ANCHOR, TEXT_NEW),
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
        print("Long posts are already being clipped. Nothing was changed.")
        return

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

    problems = []
    if MARKER not in updated:
        problems.append("the clipping did not go in")
    if ".post-text.clipped" not in updated:
        problems.append("the styling did not go in")
    if len(updated) <= len(original):
        problems.append("the file did not grow, so something was removed")

    if problems:
        stop("The upgrade did not come out right, so nothing was written.\n"
             "Problems: " + "; ".join(problems))

    with open(TARGET, "w", encoding="utf-8") as handle:
        handle.write(updated)

    print("")
    print("Done. Two changes:")
    print("  1. Styling for a clipped post and its Show link.")
    print("  2. Posts over about 260 characters are now clipped.")
    print("")
    print("Restart the app to see it:")
    print("  press Ctrl and C if it is running, then  python app.py")
    print("")
    print("Your original is in", BACKUP)


if __name__ == "__main__":
    main()
