"""Tests for picking the right Deezer artist out of a search result.

Every case below is taken from real output. Asking Deezer for one result and
keeping it gave 67 of 754 artists a name we never searched for, plus an
unknown number of exact-name collisions on top - 219 artists ended up with
under 100 fans, which no artist in a national Spotify top 200 has.

The tension the matcher has to resolve: Deezer's spelling is sometimes more
correct than ours (accents), and sometimes a deliberate impersonation
(punctuation). Both look like "close but not equal" to a naive comparison.
"""
from app.services.extractors.deezer import normalize_name, pick_best_match


def artist(name: str, fans: int, id_: int = 1) -> dict:
    return {"id": id_, "name": name, "nb_fan": fans}


class TestNormalizeName:
    def test_accents_are_ignored(self):
        # Observed real matches: Deezer had the properly accented spelling
        # while the chart source did not.
        assert normalize_name("Babasonicos") == normalize_name("Babasónicos")
        assert normalize_name("Arcángel") == normalize_name("Arcangel")
        assert normalize_name("Serú Girán") == normalize_name("Seru Giran")

    def test_case_and_surrounding_whitespace_are_ignored(self):
        assert normalize_name("  TWICE ") == normalize_name("twice")

    def test_punctuation_is_significant(self):
        # The impostors are punctuation variants. Stripping dots here would
        # collapse each of these onto the artist it impersonates, which is
        # the one thing the matcher must never do.
        assert normalize_name("Young T.H.U.G.") != normalize_name("Young Thug")
        assert normalize_name("T.W.I.C.E.") != normalize_name("TWICE")
        assert normalize_name("Duki.") != normalize_name("Duki")
        assert normalize_name("Jul!") != normalize_name("Jul")


class TestPickBestMatch:
    def test_rejects_qualified_impostor(self):
        # "Nirvana" -> "Nirvana (UK)", 170 fans. A different real band whose
        # name merely starts the same way.
        assert pick_best_match("Nirvana", [artist("Nirvana (UK)", 170)]) is None

    def test_rejects_collaboration_masquerading_as_the_artist(self):
        # "Adele" -> "Adele & The Chandeliers", 216 fans.
        assert pick_best_match("Adele", [artist("Adele & The Chandeliers", 216)]) is None
        assert pick_best_match("Björk", [artist("Björk & Toffe", 33)]) is None

    def test_rejects_punctuation_impostor_even_when_it_ranks_first(self):
        candidates = [artist("Young T.H.U.G.", 323), artist("Young Thugga", 90)]
        assert pick_best_match("Young Thug", candidates) is None

    def test_accepts_accented_spelling_of_the_same_artist(self):
        match = pick_best_match("Babasonicos", [artist("Babasónicos", 411122)])
        assert match is not None
        assert match["nb_fan"] == 411122

    def test_exact_name_collision_resolved_by_fan_count(self):
        # The original Drake failure: several artists are named exactly
        # "Drake". The chart-topping one is the most followed by orders of
        # magnitude, so fan count separates them cleanly.
        candidates = [artist("Drake", 40, id_=1), artist("Drake", 15_000_000, id_=2)]
        match = pick_best_match("Drake", candidates)
        assert match["id"] == 2

    def test_impostor_ranked_above_a_valid_match_still_loses(self):
        # Ordering must not matter - only exactness then popularity. Deezer
        # returning the impostor first is precisely the original bug.
        candidates = [artist("Nirvana (UK)", 170), artist("Nirvana", 5_000_000)]
        match = pick_best_match("Nirvana", candidates)
        assert match["nb_fan"] == 5_000_000

    def test_deaccented_impostor_loses_to_the_real_artist(self):
        # "Jão" matched a "Jao" with 9 fans. Accent-insensitivity makes both
        # eligible, so the fan-count rule is what saves this case - the two
        # rules are load-bearing together, not independently.
        candidates = [artist("Jao", 9, id_=1), artist("Jão", 800_000, id_=2)]
        assert pick_best_match("Jão", candidates)["id"] == 2

    def test_no_candidates_at_all(self):
        assert pick_best_match("Wrldleadin", []) is None

    def test_missing_fan_count_does_not_crash(self):
        # nb_fan is absent from some payloads; absent must sort below zero
        # rather than raising, so one odd record can't fail a whole batch.
        candidates = [{"id": 1, "name": "Mora"}, artist("Mora", 500, id_=2)]
        assert pick_best_match("Mora", candidates)["id"] == 2

    def test_missing_name_does_not_crash(self):
        assert pick_best_match("Mora", [{"id": 1, "nb_fan": 10}]) is None

    def test_returns_the_full_payload_not_just_a_flag(self):
        # Callers need id and picture fields off the same object.
        payload = {"id": 7, "name": "Mora", "nb_fan": 500, "picture_medium": "u"}
        assert pick_best_match("Mora", [payload]) is payload
