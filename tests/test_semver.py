"""Tests for the semantic-version comparer used by the ``Semver*`` operators.

Mirrors the JS SDK's ``parseSemver``/``compareSemver`` and the evaluation
service's ``SemverComparer`` so server-side and SDK-local evaluation agree.
"""

import pytest

from featureflip._semver import compare_semver


class TestCompareSemver:
    def test_multi_segment_regression_greater_equal(self) -> None:
        """2.10.1 >= 2.0 — the decimal path mis-parsed "2.10.1" and returned False."""
        assert compare_semver("2.10.1", "2.0", ">=") is True

    def test_multi_segment_regression_greater_than(self) -> None:
        """2.10 > 2.9 — decimal comparison reads 2.10 as 2.1 and gets this wrong."""
        assert compare_semver("2.10", "2.9", ">") is True

    def test_missing_trailing_segments_compare_as_zero(self) -> None:
        """2.0 equals 2.0.0 — missing trailing release segments compare as 0."""
        assert compare_semver("2.0", "2.0.0", "=") is True

    def test_leading_v_is_stripped(self) -> None:
        assert compare_semver("v2.1.0", "2.1.0", "=") is True

    def test_uppercase_leading_v_is_stripped(self) -> None:
        assert compare_semver("V2.1.0", "2.1.0", "=") is True

    def test_build_metadata_ignored_for_precedence(self) -> None:
        assert compare_semver("1.0.0+build.99", "1.0.0", "=") is True

    def test_leading_zeros_in_segments(self) -> None:
        assert compare_semver("1.01.0", "1.1.0", "=") is True

    def test_large_segments_do_not_overflow(self) -> None:
        big = "99999999999999999999999999999999"
        assert compare_semver("1.0.0", f"{big}.0.0", "<") is True

    def test_release_outranks_prerelease(self) -> None:
        """A version with no prerelease outranks one with a prerelease (semver §11)."""
        assert compare_semver("1.0.0", "1.0.0-alpha", ">") is True

    def test_prerelease_alphabetical_ordering(self) -> None:
        assert compare_semver("1.0.0-alpha", "1.0.0-beta", "<") is True

    def test_numeric_prerelease_below_alphanumeric(self) -> None:
        """Numeric prerelease identifiers rank below alphanumeric ones."""
        assert compare_semver("1.0.0-1", "1.0.0-alpha", "<") is True

    def test_numeric_prerelease_compared_numerically(self) -> None:
        assert compare_semver("1.0.0-2", "1.0.0-11", "<") is True

    def test_longer_prerelease_wins_when_shared_identifiers_equal(self) -> None:
        assert compare_semver("1.0.0-alpha", "1.0.0-alpha.1", "<") is True

    def test_equal_versions(self) -> None:
        assert compare_semver("1.2.3", "1.2.3", "=") is True
        assert compare_semver("1.2.3", "1.2.3", ">=") is True
        assert compare_semver("1.2.3", "1.2.3", "<=") is True
        assert compare_semver("1.2.3", "1.2.3", ">") is False
        assert compare_semver("1.2.3", "1.2.3", "<") is False

    def test_less_than_and_less_equal(self) -> None:
        assert compare_semver("1.0.0", "2.0.0", "<") is True
        assert compare_semver("1.0.0", "2.0.0", "<=") is True
        assert compare_semver("2.0.0", "1.0.0", "<") is False

    @pytest.mark.parametrize("value", ["", "   ", "not-a-version", "1.x.0", "abc"])
    def test_unparseable_value_never_matches(self, value: str) -> None:
        """An unparseable version matches nothing, like the numeric/date operators."""
        assert compare_semver(value, "1.0.0", "=") is False
        assert compare_semver(value, "1.0.0", ">=") is False
        assert compare_semver(value, "1.0.0", "<=") is False

    @pytest.mark.parametrize("target", ["", "not-a-version", "1.0.0-"])
    def test_unparseable_target_never_matches(self, target: str) -> None:
        assert compare_semver("1.0.0", target, ">=") is False
        assert compare_semver("1.0.0", target, "=") is False
