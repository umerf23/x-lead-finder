"""
Tests for the evidence check in score.py.

Purpose: prove, without spending a penny or calling an API, that the
one safeguard this tool rests on actually works.

The claim being tested is this. The scoring model must quote the words
in the post that prove the author wants to pay somebody. If that quote
cannot be found in the post, the score is capped below the review
threshold and the lead is marked as held back. A model can write a
convincing reason for a post that says no such thing. It cannot talk
its way past a string comparison.

These tests use only the standard library, so nothing extra needs
installing. Run them with (venv) showing:

    python test_score.py

Or, if you prefer the standard runner:

    python -m unittest test_score -v

Every test finishes in milliseconds. No network, no keys, no cost.
"""

import unittest

import score


class TestNormalise(unittest.TestCase):
    """Text is squashed to a comparable shape before matching."""

    def test_lowercases_and_strips_punctuation(self):
        self.assertEqual(
            score.normalise("Looking For Someone -- ASAP!!"),
            "looking for someone asap")

    def test_squeezes_repeated_whitespace(self):
        self.assertEqual(
            score.normalise("need   someone\n\tto   build"),
            "need someone to build")

    def test_handles_empty_and_missing_values(self):
        self.assertEqual(score.normalise(""), "")
        self.assertEqual(score.normalise(None), "")


class TestEvidenceSupported(unittest.TestCase):
    """The check that decides whether the model quoted or invented."""

    POST = ("Looking for a serious Automation Developer to join me long "
            "term. I'm building a business automation agency and need a "
            "technical partner to build client systems.")

    def test_exact_quote_is_accepted(self):
        self.assertTrue(score.evidence_supported(
            "Looking for a serious Automation Developer", self.POST))

    def test_quote_survives_different_case_and_punctuation(self):
        # A model often tidies punctuation while quoting accurately.
        # That must still count as quoting, not inventing.
        self.assertTrue(score.evidence_supported(
            "looking for a SERIOUS automation developer!!", self.POST))

    def test_invented_quote_is_rejected(self):
        # Nothing resembling this appears in the post. This is the
        # failure mode the whole safeguard exists to catch.
        self.assertFalse(score.evidence_supported(
            "DM me your rates and I will pay upfront today", self.POST))

    def test_empty_evidence_is_rejected(self):
        self.assertFalse(score.evidence_supported("", self.POST))
        self.assertFalse(score.evidence_supported("   ", self.POST))

    def test_mostly_matching_quote_is_accepted(self):
        # Nine of these ten words are in the post, comfortably above
        # the seven in ten the module requires.
        quote = ("looking for a serious automation developer to join "
                 "me forever")
        self.assertTrue(score.evidence_supported(quote, self.POST))

    def test_barely_matching_quote_is_rejected(self):
        # Two of these eight words appear in the post. Sharing a few
        # common words is not quoting.
        quote = "for the crypto airdrop giveaway winners announced tomorrow"
        self.assertFalse(score.evidence_supported(quote, self.POST))

    def test_threshold_is_the_documented_seven_in_ten(self):
        # Guards the constant itself. If somebody loosens this later,
        # a test should complain rather than the tool quietly getting
        # easier to fool.
        self.assertEqual(score.EVIDENCE_WORD_MATCH, 0.7)


class TestSafeScore(unittest.TestCase):
    """Whatever the model returns, the score stays a sane number."""

    def test_keeps_ordinary_values(self):
        self.assertEqual(score.safe_score(75), 75)
        self.assertEqual(score.safe_score("88"), 88)
        self.assertEqual(score.safe_score(64.7), 64)

    def test_clamps_out_of_range_values(self):
        self.assertEqual(score.safe_score(140), 100)
        self.assertEqual(score.safe_score(-20), 0)

    def test_junk_becomes_zero_rather_than_crashing(self):
        self.assertEqual(score.safe_score("very high"), 0)
        self.assertEqual(score.safe_score(None), 0)
        self.assertEqual(score.safe_score({}), 0)


class TestMergeJudgement(unittest.TestCase):
    """The rule applied to every lead before you ever see it."""

    POST = {
        "post_id": "1",
        "text": "Hiring the replacement. We're a fast-scaling DTC e-com "
                "brand looking for a full-time, brand-exclusive video editor.",
        "status": "new",
    }

    def test_proven_evidence_keeps_its_score(self):
        result = score.merge_judgement(self.POST, {
            "intent_score": 95,
            "evidence": "looking for a full-time, brand-exclusive video editor",
            "category": "direct hire",
            "reason": "Author is hiring an editor directly.",
        })
        self.assertEqual(result["intent_score"], 95)
        self.assertTrue(result["evidence_verified"])
        self.assertFalse(result["score_capped"])
        self.assertNotIn("original_score", result)

    def test_unproven_evidence_is_capped_below_the_threshold(self):
        result = score.merge_judgement(self.POST, {
            "intent_score": 95,
            "evidence": "I will pay five thousand dollars for this work",
            "category": "direct hire",
            "reason": "Author states a budget.",
        })
        self.assertEqual(result["intent_score"], score.UNPROVEN_SCORE_CAP)
        self.assertFalse(result["evidence_verified"])
        self.assertTrue(result["score_capped"])
        # The original claim is kept so the capping is auditable
        # rather than silent.
        self.assertEqual(result["original_score"], 95)

    def test_cap_lands_below_the_review_threshold(self):
        # The cap is only meaningful if it falls under the score at
        # which a lead reaches your digest.
        self.assertLess(score.UNPROVEN_SCORE_CAP, 65)

    def test_low_score_is_left_alone_even_when_unproven(self):
        # Capping a 20 to 39 would raise it. The rule only ever
        # lowers a score.
        result = score.merge_judgement(self.POST, {
            "intent_score": 20,
            "evidence": "completely unrelated invented sentence here",
        })
        self.assertEqual(result["intent_score"], 20)
        self.assertFalse(result["score_capped"])

    def test_review_marks_are_preserved(self):
        # Re-scoring must never undo a lead you already handled.
        handled = dict(self.POST, status="handled")
        result = score.merge_judgement(handled, {"intent_score": 80})
        self.assertEqual(result["status"], "handled")

    def test_original_post_is_not_modified(self):
        before = dict(self.POST)
        score.merge_judgement(self.POST, {"intent_score": 90})
        self.assertEqual(self.POST, before)

    def test_missing_fields_fall_back_safely(self):
        result = score.merge_judgement(self.POST, {})
        self.assertEqual(result["intent_score"], 0)
        self.assertEqual(result["category"], "unsorted")
        self.assertEqual(result["reason"], "")
        self.assertFalse(result["budget_signal"])

    def test_overlong_model_output_is_truncated(self):
        result = score.merge_judgement(self.POST, {
            "intent_score": 10,
            "evidence": "x" * 500,
            "category": "y" * 100,
            "reason": "z" * 400,
        })
        self.assertLessEqual(len(result["evidence"]), 300)
        self.assertLessEqual(len(result["category"]), 40)
        self.assertLessEqual(len(result["reason"]), 200)


if __name__ == "__main__":
    unittest.main(verbosity=2)
