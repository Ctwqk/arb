import sys
import types
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

qdrant_client_stub = types.ModuleType("qdrant_client")
qdrant_client_stub.AsyncQdrantClient = object
qdrant_models_stub = types.ModuleType("qdrant_client.models")
for name in (
    "Distance",
    "FieldCondition",
    "Filter",
    "MatchValue",
    "OptimizersConfigDiff",
    "PointIdsList",
    "PointStruct",
    "ScoredPoint",
    "VectorParams",
):
    setattr(qdrant_models_stub, name, object)
sys.modules.setdefault("qdrant_client", qdrant_client_stub)
sys.modules.setdefault("qdrant_client.models", qdrant_models_stub)

from matcher import _kalshi_candidate_compatible, build_match_text


class MatcherStructureTests(unittest.TestCase):
    def test_build_match_text_rewrites_generic_candidate_market(self):
        result = build_match_text(
            "kalshi",
            "Who will be the next manager of Manchester United?",
            {"yes_sub_title": "Michael Carrick"},
        )
        self.assertEqual(result, "Will Michael Carrick be the next manager of Manchester United?")

    def test_build_match_text_rewrites_generic_winner_market(self):
        result = build_match_text(
            "kalshi",
            "Who will win Hart Memorial Trophy?",
            {"yes_sub_title": "Nikita Kucherov"},
        )
        self.assertEqual(result, "Will Nikita Kucherov win Hart Memorial Trophy?")

    def test_candidate_compatibility_accepts_candidate_specific_equivalent(self):
        title = build_match_text(
            "kalshi",
            "Who will win Calder Memorial Trophy?",
            {"yes_sub_title": "Beckett Sennecke", "end_date": "2026-06-30T14:00:00Z"},
        )
        self.assertTrue(
            _kalshi_candidate_compatible(
                match_title=title,
                meta={"yes_sub_title": "Beckett Sennecke", "end_date": "2026-06-30T14:00:00Z"},
                candidate_title="Will Beckett Sennecke win the 2025–2026 NHL Calder Memorial Trophy?",
                payload={"end_date": "2026-06-30T00:00:00Z"},
                keyword_score=0.8,
            )
        )

    def test_candidate_compatibility_rejects_different_house_district(self):
        self.assertFalse(
            _kalshi_candidate_compatible(
                match_title="Will Democratic party win the House race for NJ-9?",
                meta={"yes_sub_title": "Democratic party"},
                candidate_title="Will the Democratic Party win the NJ-10 House seat?",
                payload={},
                keyword_score=0.9,
            )
        )

    def test_candidate_compatibility_rejects_single_match_vs_season_winner(self):
        self.assertFalse(
            _kalshi_candidate_compatible(
                match_title="Will FC Porto win the Liga Portugal?",
                meta={"yes_sub_title": "FC Porto"},
                candidate_title="Will FC Porto win on 2026-03-22?",
                payload={},
                keyword_score=0.8,
            )
        )

    def test_candidate_compatibility_rejects_rank_vs_relegation(self):
        self.assertFalse(
            _kalshi_candidate_compatible(
                match_title="Will Brentford finish in the top 2 in the 2025-26 English Premier League season?",
                meta={"yes_sub_title": "Brentford"},
                candidate_title="Will Brentford be relegated from the English Premier League after the 2025–26 season?",
                payload={},
                keyword_score=0.8,
            )
        )

    def test_candidate_compatibility_rejects_first_round_vs_full_election(self):
        self.assertFalse(
            _kalshi_candidate_compatible(
                match_title="Will Vicky Dávila win the next Colombian presidential election?",
                meta={"yes_sub_title": "Vicky Dávila"},
                candidate_title="Will Vicky Dávila win the 1st round of the 2026 Colombian presidential election?",
                payload={},
                keyword_score=0.8,
            )
        )

    def test_candidate_compatibility_rejects_album_vs_artist(self):
        self.assertFalse(
            _kalshi_candidate_compatible(
                match_title="Will Ed Sheeran have a #1 album this year?",
                meta={"yes_sub_title": "Ed Sheeran"},
                candidate_title="Will Ed Sheeran be the Billboard #1 top artist in 2026?",
                payload={},
                keyword_score=0.8,
            )
        )


if __name__ == "__main__":
    unittest.main()
