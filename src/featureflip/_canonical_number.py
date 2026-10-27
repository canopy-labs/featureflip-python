"""Canonical spelling of a floating-point rollout bucket value (#4236).

Same text as ECMAScript ``Number::toString`` and the Evaluation API's
``BucketValueFormatter.CanonicalNumber``, so every SDK hashes one number to one
bucket. Used only on the bucket path; condition evaluation is unaffected.
"""

from __future__ import annotations

import math
from decimal import Decimal


def canonical_number(x: float) -> str:
    if math.isnan(x):
        return "NaN"
    if math.isinf(x):
        return "Infinity" if x > 0 else "-Infinity"
    if x == 0:
        return "0"
    if x < 0:
        return "-" + canonical_number(-x)

    # repr(float) is the shortest round-trip decimal.
    _, digit_tuple, exp = Decimal(repr(x)).as_tuple()
    all_digits = "".join(str(d) for d in digit_tuple)
    digits = all_digits.rstrip("0")
    k = len(digits)
    # Value = 0.<all_digits> * 10^n; stripping trailing zeros does not move n.
    n = int(exp) + len(all_digits)

    if k <= n <= 21:
        return digits + "0" * (n - k)
    if 0 < n <= 21:
        return digits[:n] + "." + digits[n:]
    if -6 < n <= 0:
        return "0." + "0" * (-n) + digits
    e = n - 1
    mantissa = digits[0] + ("." + digits[1:] if k > 1 else "")
    return f"{mantissa}e{'+' if e >= 0 else '-'}{abs(e)}"


def canonical_decimal(d: Decimal) -> str:
    """Bucket text for a ``decimal.Decimal``: an integral value below 1e21 keeps
    its exact integer digits (like the engine's raw integer text); anything else
    goes through ``float`` and :func:`canonical_number`.

    The 1e21 cap (#4246) is where ECMAScript spelling turns exponential. Without
    it ``Decimal("1E+999999999")`` would expand to a billion digits per evaluation."""
    if d.is_finite() and (d.is_zero() or d.adjusted() < 21) and d == d.to_integral_value():
        return str(int(d))
    return canonical_number(float(d))
