import unittest

from llm import _build_kalshi_contract_text, _prevalidate_pair


class ValidatorHeuristicTests(unittest.TestCase):
    def test_candidate_specific_award_question_is_synthesized(self):
        self.assertEqual(
            _build_kalshi_contract_text("Who will win Hart Memorial Trophy?", "Connor Bedard"),
            "Will Connor Bedard win Hart Memorial Trophy?",
        )

    def test_prevalidate_tie_vs_draw(self):
        self.assertTrue(
            _prevalidate_pair(
                kalshi_title="Germany vs Ghana Winner?",
                kalshi_yes_outcome="Tie",
                kalshi_end_date="2026-06-08T12:00:00Z",
                poly_title="Will Germany vs. Ghana end in a draw?",
                poly_end_date="2026-06-08T12:00:00Z",
            )
        )

    def test_prevalidate_candidate_specific_generic_kalshi_title(self):
        self.assertTrue(
            _prevalidate_pair(
                kalshi_title="Who will win the 2026 D.C. Democratic Mayoral Primary?",
                kalshi_yes_outcome="Gary Goodweather",
                kalshi_end_date="2026-06-03T00:00:00Z",
                poly_title="Will Gary Goodweather win the 2026 Democratic D.C. Mayoral Primary?",
                poly_end_date="2026-06-03T00:00:00Z",
            )
        )

    def test_prevalidate_ignores_optional_year_and_league_prefix(self):
        self.assertTrue(
            _prevalidate_pair(
                kalshi_title="Will Brian Harman win the Masters Tournament?",
                kalshi_yes_outcome="",
                kalshi_end_date="2026-04-13T00:00:00Z",
                poly_title="Will Brian Harman win the 2026 Masters tournament?",
                poly_end_date="2026-04-13T00:00:00Z",
            )
        )
        self.assertTrue(
            _prevalidate_pair(
                kalshi_title="Will Porto win the Europa League?",
                kalshi_yes_outcome="",
                kalshi_end_date="2026-05-31T00:00:00Z",
                poly_title="Will Porto win the 2025-26 UEFA Europa League?",
                poly_end_date="2026-05-31T00:00:00Z",
            )
        )

    def test_prevalidate_allows_album_wording_variation(self):
        self.assertTrue(
            _prevalidate_pair(
                kalshi_title="Will Olivia Rodrigo release a new album in 2026?",
                kalshi_yes_outcome="",
                kalshi_end_date="2026-12-31T00:00:00Z",
                poly_title="Will Olivia Rodrigo release an album in 2026?",
                poly_end_date="2026-12-31T00:00:00Z",
            )
        )

    def test_prevalidate_does_not_auto_accept_year_conflict(self):
        self.assertIsNone(
            _prevalidate_pair(
                kalshi_title="Will Olivia Rodrigo release an album in 2025?",
                kalshi_yes_outcome="",
                kalshi_end_date="2025-12-31T00:00:00Z",
                poly_title="Will Olivia Rodrigo release an album in 2026?",
                poly_end_date="2026-12-31T00:00:00Z",
            )
        )


if __name__ == "__main__":
    unittest.main()
