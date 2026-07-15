"""
tests/test_market_classifier.py
─────────────────────────────────
Regression coverage for a real bug found while investigating Pakistan Jobs
Bank categorization: classify_job()'s title-substring check ("phrase in
title_lower") has no word-boundary awareness, so a short keyword like
"cto" (it.product) matches any title that merely CONTAINS that letter
sequence - "Assistant Director" and "Inspectors" both false-positived into
it.product purely because "director"/"inspectors" contain "cto" embedded
inside them (dire-CTO-r, inspe-CTO-rs). Confirmed live against real
production data: 144 of 1,331 jobs tagged it.product (~11%) have
director/inspector/sector/doctor in the title.

Fixed by matching keyword phrases with regex word boundaries instead of
plain substring containment, so "cto" only matches as its own word/token,
never embedded inside an unrelated longer word.
"""
from src.market_classifier import classify_job


class TestKeywordMatchingRespectsWordBoundaries:
    def test_short_acronym_keyword_does_not_match_inside_unrelated_word(self):
        """'cto' (it.product) must not match merely because the title
        contains those letters in sequence inside a different word."""
        result = classify_job("Assistant Director - QEC (BPS-17)")
        assert result.market_id != "it.product", (
            f"'Assistant Director' false-matched it.product via evidence "
            f"{result.evidence!r} - 'director' contains 'cto' embedded in it"
        )

    def test_inspectors_does_not_match_cto(self):
        result = classify_job("Inspectors (BPS-16)")
        assert result.market_id != "it.product", (
            f"'Inspectors' false-matched it.product via evidence {result.evidence!r} "
            f"- 'inspectors' contains 'cto' embedded in it"
        )

    def test_genuine_cto_title_still_matches(self):
        """The fix must not throw out the true positive along with the
        false one - a real CTO title must still classify as it.product."""
        result = classify_job("CTO - Chief Technology Officer")
        assert result.market_id == "it.product"
        assert any("cto" in e for e in result.evidence)

    def test_cto_as_standalone_word_in_longer_title_still_matches(self):
        result = classify_job("Seeking a CTO for our startup")
        assert result.market_id == "it.product"

    def test_network_administrator_still_matches_it_software(self):
        """Sanity check: a real multi-word keyword phrase (unaffected by
        the word-boundary bug, since a phrase containing a space can't hide
        inside a single unrelated word) must be untouched by the fix."""
        result = classify_job("Network Administrator (PPS-06)")
        assert result.market_id == "it.software"

    def test_dotnet_keyword_with_regex_metacharacters_still_matches(self):
        """Keywords containing regex-special characters (., +, #) must be
        escaped, not interpreted as regex syntax, by the boundary fix."""
        result = classify_job("Senior .NET Developer")
        assert result.market_id is not None
        assert any(".net" in e.lower() for e in result.evidence)
