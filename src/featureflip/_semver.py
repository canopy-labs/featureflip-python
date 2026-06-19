"""Semantic-version comparison for the ``Semver*`` condition operators.

Tolerant of real-world version strings: an optional leading ``v``, an arbitrary
number of dot-separated numeric segments (missing trailing segments compare as 0,
so ``2.0`` == ``2.0.0``), an optional ``-prerelease`` suffix (lower precedence than
the release it qualifies), and ``+build`` metadata (ignored for precedence). Numeric
segments are compared digit-by-digit, so arbitrarily large version numbers never
overflow. A value whose release core is missing or non-numeric is "not a version"
and matches nothing — mirroring how the numeric and date/time operators treat
unparseable input.

Mirrors the evaluation service's ``SemverComparer`` and the JS SDK's
``parseSemver``/``compareSemver`` so server-side and SDK-local evaluation agree.
"""

from __future__ import annotations

# (release segments, prerelease identifiers) — both already validated.
_SemverParts = tuple[list[str], list[str]]


def _is_all_digits(s: str) -> bool:
    return len(s) > 0 and all("0" <= c <= "9" for c in s)


def _parse_semver(value: str) -> _SemverParts | None:
    s = value.strip()
    if not s:
        return None

    # Optional leading "v"/"V".
    if s[0] in ("v", "V"):
        s = s[1:]

    # Build metadata ("+...") does not affect precedence.
    plus = s.find("+")
    if plus >= 0:
        s = s[:plus]

    # Split the release core from the optional "-prerelease" suffix.
    dash = s.find("-")
    if dash >= 0:
        core = s[:dash]
        pre = s[dash + 1:]
        if not pre:
            return None  # trailing "-" with no identifiers is malformed
        prerelease = pre.split(".")
        if any(len(identifier) == 0 for identifier in prerelease):
            return None
    else:
        core = s
        prerelease = []

    if not core:
        return None

    release = core.split(".")
    if any(not _is_all_digits(seg) for seg in release):
        return None

    return release, prerelease


def _compare_numeric_string(a: str, b: str) -> int:
    """Compare two all-digit strings as non-negative integers without parsing
    (overflow-free): strip leading zeros, then the longer string is the larger
    number; equal lengths compare ordinally."""
    a = a.lstrip("0")
    b = b.lstrip("0")
    if len(a) != len(b):
        return -1 if len(a) < len(b) else 1
    if a < b:
        return -1
    if a > b:
        return 1
    return 0


def _compare_prerelease_identifier(a: str, b: str) -> int:
    a_num = _is_all_digits(a)
    b_num = _is_all_digits(b)

    # Numeric identifiers always have lower precedence than alphanumeric ones.
    if a_num and b_num:
        return _compare_numeric_string(a, b)
    if a_num:
        return -1
    if b_num:
        return 1
    if a < b:
        return -1
    if a > b:
        return 1
    return 0


def _compare_prerelease(a: list[str], b: list[str]) -> int:
    # A version with no prerelease has higher precedence than one with a prerelease.
    if not a and not b:
        return 0
    if not a:
        return 1
    if not b:
        return -1

    for ida, idb in zip(a, b, strict=False):  # compare shared identifiers only
        cmp = _compare_prerelease_identifier(ida, idb)
        if cmp != 0:
            return cmp

    # All shared identifiers equal: the longer prerelease has higher precedence.
    if len(a) == len(b):
        return 0
    return -1 if len(a) < len(b) else 1


def _compare_parts(a: _SemverParts, b: _SemverParts) -> int:
    a_release, a_prerelease = a
    b_release, b_prerelease = b
    for i in range(max(len(a_release), len(b_release))):
        seg_a = a_release[i] if i < len(a_release) else "0"
        seg_b = b_release[i] if i < len(b_release) else "0"
        cmp = _compare_numeric_string(seg_a, seg_b)
        if cmp != 0:
            return cmp
    return _compare_prerelease(a_prerelease, b_prerelease)


def compare_semver(value: str, target: str, op: str) -> bool:
    """Compare ``value`` to ``target`` as semantic versions.

    ``op`` is one of ``">"``, ``"<"``, ``">="``, ``"<="``, ``"="``. Returns False
    when either operand is not a parseable version.
    """
    left = _parse_semver(value)
    right = _parse_semver(target)
    if left is None or right is None:
        return False

    c = _compare_parts(left, right)
    if op == ">":
        return c > 0
    if op == "<":
        return c < 0
    if op == ">=":
        return c >= 0
    if op == "<=":
        return c <= 0
    if op == "=":
        return c == 0
    return False
