"""
Trinocular belief model — Quan, Heidemann & Pradkin, "Trinocular: Understanding
Internet Reliability Through Adaptive Probing", SIGCOMM 2013.
https://ant.isi.edu/~johnh/PAPERS/Quan13c.pdf

The paper tracks each IPv4 /24 block as one of three states (its namesake:
up, down, or uncertain) using Bayesian belief updated from ICMP probes to a
block's historically-active addresses, and paces future probing off how
confident that belief currently is (§4). We reuse that exact model here, but
at the granularity this project actually operates at: each COUNTRY's
configured probe targets (backend/probe_targets.py) stand in for a block's
"ever active addresses" E(b). Two parameters are adapted rather than copied
verbatim, because they assume /24-scale sampling (E(b) >= 15, |b| up to
256) and we have only a handful of hand-picked targets per country:

  - availability A(E(b)) is normally seeded from a multi-year IPv4 census
    (§4.4). We have no such census, so we start from a neutral prior and
    track it online as an exponential moving average of observed responses
    (this is listed as future work in the paper itself, §4.4 "Evolution").
  - the down-state emission probability (1-ell)/|b| degenerates for |b|<~8
    (a single-target country would make a positive probe response almost
    uninformative). EFFECTIVE_BLOCK_FLOOR keeps that term sane; it has no
    effect once a country has >= EFFECTIVE_BLOCK_FLOOR configured targets.

Everything else — the belief update equations, the up/down/uncertain
thresholds, the adaptive-probe cap, and the recovery-probing formula for
low-availability blocks — is taken directly from paper §4.2-4.3.
"""

import math

# §4.2: packet/reply loss rate ("a reasonable but arbitrary value" per the paper)
LOSS_RATE = 0.01

# §4.2: belief and availability are capped away from 0/1 so the Bayesian
# update never divides by zero (a probability of exactly 0 or 1 makes a
# conditional term vanish).
MIN_PROB = 0.01
MAX_PROB = 0.99

# §4.3: "We classify a block as down when B(U) < 0.1, and up when B(U) > 0.9"
DOWN_THRESHOLD = 0.1
UP_THRESHOLD   = 0.9

# §4.3: "we send at most 15 total probes per round (1 periodic and up to 14 additional adaptive)"
MAX_PROBES_PER_ROUND = 15

# See module docstring — our target lists are much smaller than a /24's
# E(b) >= 15 requirement; this floor keeps the down-state emission term
# from degenerating at |b| = 1-3.
EFFECTIVE_BLOCK_FLOOR = 8

# Starting priors for a country we've never probed before. "Since most of
# the Internet is always up, we set belief to indicate all blocks are up
# on startup" (§4.4).
INITIAL_BELIEF_UP   = MAX_PROB
INITIAL_AVAILABILITY = 0.9


def _clamp(x: float, lo: float = MIN_PROB, hi: float = MAX_PROB) -> float:
    return max(lo, min(hi, x))


def bayes_update(belief_up: float, availability: float, block_size: int, positive: bool) -> float:
    """
    One Bayesian belief update from a single probe observation (§4.2, Table 1
    and the B' equations). Returns the new belief that the country is UP.
    """
    a = _clamp(availability)
    b_up   = _clamp(belief_up)
    b_down = 1 - b_up
    n = max(block_size, EFFECTIVE_BLOCK_FLOOR)

    if positive:
        p_given_up, p_given_down = a, (1 - LOSS_RATE) / n
    else:
        p_given_up, p_given_down = (1 - a), 1 - (1 - LOSS_RATE) / n

    numerator   = p_given_up * b_up
    denominator = numerator + p_given_down * b_down
    if denominator <= 0:
        return b_up
    return _clamp(numerator / denominator)


def classify_belief(belief_up: float) -> str:
    """§4.3 thresholds: down when B(U) < 0.1, up when B(U) > 0.9, else uncertain."""
    if belief_up < DOWN_THRESHOLD:
        return "down"
    if belief_up > UP_THRESHOLD:
        return "up"
    return "uncertain"


def recovery_probe_budget(availability: float, max_probes: int = MAX_PROBES_PER_ROUND) -> int:
    """
    §4.3 "Recovery probing": for a currently-down, intermittently-active
    block, consecutive misses can just mean repeatedly hitting a vacant
    address rather than confirming the block is still down. Choose k probes
    so the false-negative rate (1-A)^k is <= 20%.
    """
    a = _clamp(availability)
    if a >= MAX_PROB:
        return 1
    k = math.ceil(math.log(0.2) / math.log(1 - a))
    return max(1, min(max_probes, k))


def update_availability(availability: float, positive: bool, alpha: float = 0.15) -> float:
    """
    Online analog of §4.4's A(E(b)): an exponential moving average of the
    response rate, updated only from observations made while the country is
    believed up (see caller — outages should not drag A down, matching the
    paper's own "outages are very rare, they have negligible influence on
    A(E(b))").
    """
    observed = 1.0 if positive else 0.0
    return _clamp(availability * (1 - alpha) + observed * alpha)
