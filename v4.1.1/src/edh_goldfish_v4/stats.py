"""Statistics with verified formulas only.

- Wilson score interval (audit-verified correct in v3; reproduces the
  reference worked example 4/5 -> 37.55%..96.38%). Formula verified
  online 2026-10-31 against Wikipedia, "Wilson score interval" (the
  score-test inversion form used here).
- Karsten land formula and pip table (audit Section D: coefficients and
  turns 2-6 values verified). The v3 table's turn-1 double/triple entries
  (31/38) were invented (audit item 24) and are NOT carried over: a
  multi-pip spell's target turn is at least its pip count.
  v4.0.2 online check: the land-count regression
  31.42 + 3.13*AvgMV - 0.28*(cheap ramp/draw) matches Karsten's
  published 99-card formula (TCGplayer Infinite, "How Many Lands Do You
  Need in Your Deck? An Updated Analysis", 2022) as quoted by multiple
  independent sources. The colored-source TABLE could not be re-verified
  against Karsten's primary article online (both TCGplayer and
  ChannelFireball fetches failed 2026-10-31) and some secondary sources
  quote higher values for individual cells; the table is therefore kept
  exactly as audit-verified against Volume 1 SS3.1. See BUILD_REPORT.
- Exact order-statistic median CI (binomial ranks) — honestly exact,
  unlike v3's normal approximation labeled 'exact' (audit item 22).
- Percentile bootstrap CIs for means / paired differences (percentile
  method: the alpha/2 and 1-alpha/2 quantiles of the bootstrap
  distribution; Efron 1979 — verified online 2026-10-31 against the
  standard references, incl. SciPy's bootstrap documentation).
- v4.0.2 additions, each verified online 2026-10-31 before
  implementation (see BUILD_REPORT for the source per function):
  hypergeometric probabilities (Wikipedia PMF), Wilcoxon signed-rank
  (Wikipedia procedure; Penn State STAT 415 worked example),
  McNemar's test (Wikipedia; R mcnemar.test worked examples), and the
  Mann-Whitney U test (Wikipedia statistic definition; fully worked
  textbook examples).
"""

from __future__ import annotations

import math
import random

Z95 = 1.959963984540054


def wilson(k: int, n: int, z: float = Z95) -> tuple[float, float]:
    """Wilson score interval for k successes in n trials -> (lo, hi)."""
    if n == 0:
        return (0.0, 0.0)
    p = k / n
    z2 = z * z
    denom = 1.0 + z2 / n
    center = (p + z2 / (2 * n)) / denom
    half = (z / denom) * math.sqrt(p * (1 - p) / n + z2 / (4 * n * n))
    return (max(0.0, center - half), min(1.0, center + half))


def mean(xs) -> float:
    xs = list(xs)
    return sum(xs) / len(xs) if xs else 0.0


def percentile(xs, q: float) -> float:
    """Linear-interpolation percentile (numpy 'linear' convention)."""
    xs = sorted(xs)
    if not xs:
        return 0.0
    if len(xs) == 1:
        return float(xs[0])
    rank = (q / 100.0) * (len(xs) - 1)
    lo = math.floor(rank)
    hi = math.ceil(rank)
    if lo == hi:
        return float(xs[int(rank)])
    frac = rank - lo
    return xs[lo] * (1 - frac) + xs[hi] * frac


def median(xs) -> float:
    return percentile(xs, 50)


def _binom_cdf(k: int, n: int, p: float = 0.5) -> float:
    if k < 0:
        return 0.0
    if k >= n:
        return 1.0
    return sum(math.comb(n, i) * (p ** i) * ((1 - p) ** (n - i))
               for i in range(0, k + 1))


def median_ci_exact(xs, alpha: float = 0.05) -> tuple[float, float]:
    """Exact order-statistic CI for the median: ranks k1<k2 chosen from
    Bin(n, 0.5) so that P(k1 <= Bin <= k2) >= 1-alpha (standard
    distribution-free construction)."""
    xs = sorted(xs)
    n = len(xs)
    if n == 0:
        return (0.0, 0.0)
    if n == 1:
        return (float(xs[0]), float(xs[0]))
    # lower rank: largest r (1-indexed) with P(Bin <= r-1) <= alpha/2
    r_lo = 1
    for r in range(1, n + 1):
        if _binom_cdf(r - 1, n) <= alpha / 2:
            r_lo = r
        else:
            break
    # upper rank: smallest r with P(Bin <= r) >= 1 - alpha/2
    r_hi = n
    for r in range(1, n + 1):
        if _binom_cdf(r, n) >= 1 - alpha / 2:
            r_hi = r
            break
    return (float(xs[r_lo - 1]), float(xs[r_hi - 1]))


def bootstrap_mean_ci(xs, rng: random.Random, b: int = 1000,
                      alpha: float = 0.05) -> tuple[float, float]:
    """Percentile bootstrap CI for the mean (used for paired regime
    differences as well — pass the per-trial differences)."""
    xs = list(xs)
    n = len(xs)
    if n == 0:
        return (0.0, 0.0)
    if n == 1:
        return (float(xs[0]), float(xs[0]))
    means = []
    for _ in range(b):
        s = 0.0
        for _ in range(n):
            s += xs[rng.randrange(n)]
        means.append(s / n)
    means.sort()
    lo = means[int((alpha / 2) * b)]
    hi = means[min(b - 1, int((1 - alpha / 2) * b))]
    return (lo, hi)


# --- Karsten (audit Section D: verified) --------------------------------

def karsten_lands(avg_mv: float, cheap_ramp: int, cantrips: int) -> float:
    """31.42 + 3.13*AvgMV - 0.28*(cheap ramp + cantrips)."""
    return 31.42 + 3.13 * avg_mv - 0.28 * (cheap_ramp + cantrips)


# Karsten 90% colored-source table, 99-card decks: sources needed to cast
# a spell with `pips` of one color by `turn`. Rows 2-6 verified against
# Volume 1 SS3.1. Row 1 keeps only the single-pip value (19); the v3
# turn-1 double/triple values (31/38) were invented and are omitted.
KARSTEN_PIP_TABLE: dict[int, dict[int, int]] = {
    1: {1: 19},
    2: {1: 18, 2: 29, 3: 36},
    3: {1: 16, 2: 26, 3: 34},
    4: {1: 15, 2: 23, 3: 30},
    5: {1: 14, 2: 21, 3: 27},
    6: {1: 13, 2: 19, 3: 25},
}


# --- v4.0.2: verified Tier 1 statistical machinery -----------------------
#
# Every function below was verified online against an authoritative
# source BEFORE implementation (Brandon's math gate, 2026-10-31); the
# BUILD_REPORT v4.0.2 section records the source per function.


def _norm_sf(z: float) -> float:
    """Survival function of the standard normal distribution."""
    return 0.5 * math.erfc(z / math.sqrt(2.0))


def _chi2_sf_df1(x: float) -> float:
    """Survival function of chi-squared with 1 degree of freedom."""
    if x <= 0:
        return 1.0
    return math.erfc(math.sqrt(x / 2.0))


def _binom_pmf(k: int, n: int, p: float = 0.5) -> float:
    """Binomial PMF in log space (exact comb * p^k underflows/overflows
    for large n, e.g. McNemar tables with 1000+ discordant pairs)."""
    if k < 0 or k > n:
        return 0.0
    logp = (math.lgamma(n + 1) - math.lgamma(k + 1) - math.lgamma(n - k + 1)
            + k * math.log(p) + (n - k) * math.log1p(-p))
    return math.exp(logp)


def rankdata(values) -> list[float]:
    """Ranks (1-based) with ties receiving the average of the ranks they
    span — the standard convention (Wikipedia, Wilcoxon / Mann-Whitney)."""
    vals = list(values)
    order = sorted(range(len(vals)), key=lambda i: vals[i])
    ranks = [0.0] * len(vals)
    i = 0
    while i < len(order):
        j = i
        while j + 1 < len(order) and vals[order[j + 1]] == vals[order[i]]:
            j += 1
        avg = (i + 1 + j + 1) / 2.0
        for k in range(i, j + 1):
            ranks[order[k]] = avg
        i = j + 1
    return ranks


def _tie_correction(values) -> float:
    """Sum of (t^3 - t) over tie groups of size t (0 when no ties)."""
    counts: dict = {}
    for v in values:
        counts[v] = counts.get(v, 0) + 1
    return float(sum(t ** 3 - t for t in counts.values() if t > 1))


# -- Hypergeometric (T1-01) ------------------------------------------------
# PMF P(X = k) = C(K, k) * C(N-K, n-k) / C(N, n), sampling without
# replacement (Wikipedia, "Hypergeometric distribution" — verified
# 2026-10-31; worked example there/independent calculators:
# P(X = 2) for N=52, K=13, n=5 is 0.27428).


def hypergeom_pmf(k: int, N: int, K: int, n: int) -> float:
    """P(X = k) for X ~ Hypergeometric(N, K, n): k successes in n draws
    without replacement from a population of N containing K successes."""
    if k < max(0, n + K - N) or k > min(K, n):
        return 0.0
    return math.comb(K, k) * math.comb(N - K, n - k) / math.comb(N, n)


def hypergeom_ge(k: int, N: int, K: int, n: int) -> float:
    """P(X >= k) for X ~ Hypergeometric(N, K, n)."""
    if k <= 0:
        return 1.0
    top = min(K, n)
    if k > top:
        return 0.0
    return sum(hypergeom_pmf(i, N, K, n) for i in range(k, top + 1))


# -- Wilcoxon signed-rank (T1-19) -------------------------------------------
# Procedure (Wikipedia, "Wilcoxon signed-rank test" — verified
# 2026-10-31): drop zero differences; rank |d| with average ranks for
# ties; W+ = sum of ranks of positive differences. Exact p-values use
# the sign-flip distribution conditional on the observed absolute ranks:
# doubling the (possibly averaged) ranks makes them integers, so the
# exact distribution of 2*W+ is a subset-sum distribution, computed by
# DP. Used for n <= 50 pairs. Beyond that, the standard normal
# approximation with tie correction and continuity correction
# (mean n(n+1)/4; variance n(n+1)(2n+1)/24 - sum(t^3-t)/48).

WILCOXON_EXACT_MAX_N = 50


def wilcoxon_signed_rank(differences) -> dict:
    diffs = [d for d in differences if d != 0]
    n = len(diffs)
    if n == 0:
        return {"n": 0, "w_plus": 0.0, "w_minus": 0.0,
                "p_two_sided": 1.0, "method": "no nonzero differences"}
    absvals = [abs(d) for d in diffs]
    ranks = rankdata(absvals)
    w_plus = sum(r for r, d in zip(ranks, diffs) if d > 0)
    w_minus = sum(r for r, d in zip(ranks, diffs) if d < 0)
    if n <= WILCOXON_EXACT_MAX_N:
        doubled = [int(round(2 * r)) for r in ranks]
        total2 = sum(doubled)
        # DP over subset sums of doubled ranks: dist[s] = P(2*W+ = s).
        dist = [0.0] * (total2 + 1)
        dist[0] = 1.0
        cur_max = 0
        for v in doubled:
            for s in range(cur_max, -1, -1):
                if dist[s]:
                    dist[s + v] += dist[s] * 0.5
                    dist[s] *= 0.5
            cur_max += v
        obs2 = int(round(2 * w_plus))
        p_le = sum(dist[:obs2 + 1])
        p_ge = sum(dist[obs2:])
        p = min(1.0, 2.0 * min(p_le, p_ge))
        return {"n": n, "w_plus": w_plus, "w_minus": w_minus,
                "p_two_sided": p, "method": "exact sign-flip distribution"}
    mean_w = n * (n + 1) / 4.0
    var_w = (n * (n + 1) * (2 * n + 1) / 24.0
             - _tie_correction(absvals) / 48.0)
    if var_w <= 0:
        return {"n": n, "w_plus": w_plus, "w_minus": w_minus,
                "p_two_sided": 1.0, "method": "normal approximation (zero variance)"}
    z = (abs(w_plus - mean_w) - 0.5) / math.sqrt(var_w)
    p = min(1.0, 2.0 * _norm_sf(max(0.0, z)))
    return {"n": n, "w_plus": w_plus, "w_minus": w_minus,
            "p_two_sided": p,
            "method": "normal approximation with tie and continuity correction"}


# -- McNemar's test (T1-20) ---------------------------------------------------
# Statistic chi^2 = (b - c)^2 / (b + c) on the discordant pairs of a
# paired 2x2 table, chi-squared with 1 df (Wikipedia, "McNemar's test"
# — verified 2026-10-31). The exact two-sided p-value is the binomial
# test on the discordants: 2 * P(X <= min(b, c)) for
# X ~ Binomial(b + c, 0.5) (Wikipedia's stated exact form; recommended
# there when b + c < 25). Both are reported; the chi-squared figure is
# the uncorrected statistic (Edwards' continuity-corrected variant is
# documented in BUILD_REPORT but is not the primary figure).


def mcnemar(b: int, c: int) -> dict:
    n = b + c
    if n == 0:
        return {"b": b, "c": c, "discordant": 0, "chi2": 0.0,
                "p_chi2": 1.0, "p_exact": 1.0}
    chi2 = (b - c) ** 2 / n
    p_exact = min(1.0, 2.0 * sum(_binom_pmf(k, n) for k in range(0, min(b, c) + 1)))
    return {"b": b, "c": c, "discordant": n, "chi2": chi2,
            "p_chi2": _chi2_sf_df1(chi2), "p_exact": p_exact}


# -- Mann-Whitney U (T1-21) ---------------------------------------------------
# U1 = n1*n2 + n1(n1+1)/2 - R1 (R1 = rank sum of sample 1 in the pooled
# ranking, average ranks for ties); U2 = n1*n2 - U1; U = min(U1, U2)
# (Wikipedia, "Mann-Whitney U test" — verified 2026-10-31). Exact
# two-sided p-values use the rank-sum subset distribution (tie-free
# samples only, N <= 40); otherwise the standard normal approximation
# with tie correction and continuity correction:
# Var(U) = n1*n2/12 * ((N+1) - sum(t^3-t) / (N*(N-1))).

MWU_EXACT_MAX_N = 40


def mann_whitney_u(xs, ys) -> dict:
    xs = list(xs)
    ys = list(ys)
    n1, n2 = len(xs), len(ys)
    if n1 == 0 or n2 == 0:
        return {"n1": n1, "n2": n2, "u1": 0.0, "u2": 0.0, "u": 0.0,
                "p_two_sided": 1.0, "method": "empty sample"}
    pooled = xs + ys
    ranks = rankdata(pooled)
    r1 = sum(ranks[:n1])
    u1 = n1 * n2 + n1 * (n1 + 1) / 2.0 - r1
    u2 = n1 * n2 - u1
    u = min(u1, u2)
    has_ties = len(set(pooled)) != len(pooled)
    N = n1 + n2
    if not has_ties and N <= MWU_EXACT_MAX_N:
        # Exact distribution of the rank sum of a uniformly random
        # n1-subset of {1..N}, by DP over (items chosen, sum).
        total = N * (N + 1) // 2
        dp = [[0.0] * (total + 1) for _ in range(n1 + 1)]
        dp[0][0] = 1.0
        for v in range(1, N + 1):
            for k in range(min(v, n1), 0, -1):
                row, prev = dp[k], dp[k - 1]
                for s in range(v, total + 1):
                    if prev[s - v]:
                        row[s] += prev[s - v]
        # normalize: each subset equally likely
        tot = math.comb(N, n1)
        dist = [x / tot for x in dp[n1]]
        obs = int(round(r1))
        p_le = sum(dist[:obs + 1])
        p_ge = sum(dist[obs:])
        p = min(1.0, 2.0 * min(p_le, p_ge))
        return {"n1": n1, "n2": n2, "u1": u1, "u2": u2, "u": u,
                "p_two_sided": p, "method": "exact rank-sum distribution"}
    mean_u = n1 * n2 / 2.0
    var_u = (n1 * n2 / 12.0) * ((N + 1) - _tie_correction(pooled) / (N * (N - 1)))
    if var_u <= 0:
        return {"n1": n1, "n2": n2, "u1": u1, "u2": u2, "u": u,
                "p_two_sided": 1.0,
                "method": "normal approximation (zero variance)"}
    z = (abs(u - mean_u) - 0.5) / math.sqrt(var_u)
    p = min(1.0, 2.0 * _norm_sf(max(0.0, z)))
    return {"n1": n1, "n2": n2, "u1": u1, "u2": u2, "u": u,
            "p_two_sided": p,
            "method": "normal approximation with tie and continuity correction"}


def karsten_required_sources(mv: int, pips: int) -> int | None:
    """Sources needed for a spell with `pips` of one color, targeted at
    its natural cast turn: max(pip count, mana value), clamped to 6."""
    if pips <= 0:
        return None
    turn = max(pips, min(6, max(1, mv)))
    row = KARSTEN_PIP_TABLE.get(turn, KARSTEN_PIP_TABLE[6])
    return row[min(3, pips)]
