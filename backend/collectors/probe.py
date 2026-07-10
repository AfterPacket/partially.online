"""
Active probe collector — confirmation only.

Combines:
  HTTP probe  – tries to GET a URL; classifies 2xx/3xx/4xx/timeout/refused/ssl
  TCP probe   – raw asyncio.open_connection() on specified ports;
                classifies open/refused/timeout

Per-country verdicts (up / down / uncertain) come from the Trinocular belief
model in collectors/trinocular.py — one probe at a time, with belief carried
across cycles, rather than a single-shot snapshot each round. See that
module's docstring for the model and how it's adapted to country-level
target lists instead of /24 blocks.

Separately from up/down, we also classify *why* probes look bad — reachable
but blocked (censorship-shaped) vs. reachable-then-tampered (SSL/MITM-shaped)
vs. genuinely unreachable (shutdown-shaped) — see _interference_signal().
Trinocular itself has nothing to say about this; it only distinguishes up
from down. analyzer.py uses this signal to refine an event's category.

This collector NEVER creates OutageEvent records. It only sets
probe_confirmed / resolves events via confirm_events_with_probe().
"""

import asyncio
import logging
import random
from typing import Optional

import httpx
from sqlalchemy.orm import Session

from . import trinocular
from .base import BaseCollector
from ..models import CountryBelief
from ..probe_targets import GLOBAL_ANCHORS, COUNTRY_TARGETS

log = logging.getLogger(__name__)

HTTP_TIMEOUT = 10.0
TCP_TIMEOUT  = 6.0

_SEM = asyncio.Semaphore(3)

_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/126.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,*/*;q=0.9",
    "Accept-Language": "en-US,en;q=0.9",
}


# ── Low-level probes ──────────────────────────────────────────────────────────

async def _http_probe(url: str, client: httpx.AsyncClient) -> dict:
    """HTTP GET. Returns status: ok | blocked | timeout | refused | ssl_error | error"""
    async with _SEM:
        try:
            r = await client.get(url, timeout=HTTP_TIMEOUT, follow_redirects=True)
            return {"status": "ok" if r.status_code < 400 else "blocked",
                    "code": r.status_code, "method": "http"}
        except httpx.TimeoutException:
            return {"status": "timeout", "method": "http"}
        except httpx.ConnectError as exc:
            msg = str(exc).lower()
            if "refused" in msg or "rst" in msg:
                return {"status": "refused", "method": "http"}
            if "ssl" in msg or "certificate" in msg or "tls" in msg:
                return {"status": "ssl_error", "method": "http", "detail": str(exc)[:60]}
            return {"status": "error", "method": "http", "detail": str(exc)[:60]}
        except Exception as exc:
            return {"status": "error", "method": "http", "detail": str(exc)[:60]}


async def _tcp_probe(host: str, port: int) -> dict:
    """
    Raw TCP connect attempt.

    open    – connection established      -> host is UP at network level
    refused – TCP RST received            -> host is UP, port closed (private/normal)
    timeout – no response within timeout  -> ambiguous: DOWN or silent firewall DROP
    error   – DNS/routing failure         -> possible network-level issue
    """
    async with _SEM:
        try:
            r, w = await asyncio.wait_for(
                asyncio.open_connection(host, port),
                timeout=TCP_TIMEOUT,
            )
            w.close()
            try:
                await asyncio.wait_for(w.wait_closed(), timeout=2)
            except Exception:
                pass
            return {"status": "open", "port": port, "method": "tcp"}
        except ConnectionRefusedError:
            return {"status": "refused", "port": port, "method": "tcp"}
        except asyncio.TimeoutError:
            return {"status": "timeout", "port": port, "method": "tcp"}
        except Exception as exc:
            return {"status": "error",  "port": port, "method": "tcp",
                    "detail": str(exc)[:60]}


# ── Target flattening ─────────────────────────────────────────────────────────

def _flatten_targets(info: dict) -> list:
    """
    Expand a country's configured targets into individual probe units (one
    per URL, one per IP:port) — this is our analog of Trinocular's E(b), the
    set of addresses probed for one block. Shuffled per round so repeated
    adaptive probes within a round, and successive rounds, don't always
    hit the same target first (§4.3: "probe addresses ... in a pseudorandom
    order, both to gather information from many addresses and to spread the
    reply burden").
    """
    units = []
    for t in info.get("targets", []):
        if "url" in t:
            units.append({"kind": "http", "url": t["url"], "desc": t.get("desc")})
        elif "ip" in t:
            for port in t.get("ports", [80]):
                units.append({"kind": "tcp", "ip": t["ip"], "port": port,
                              "desc": f"{t.get('desc')} :{port}"})
    random.shuffle(units)
    return units


async def _fire_probe(unit: dict, client: httpx.AsyncClient) -> dict:
    if unit["kind"] == "http":
        r = await _http_probe(unit["url"], client)
    else:
        r = await _tcp_probe(unit["ip"], unit["port"])
    return {"target": unit["desc"], **r}


# ── Interference-flavor classification (independent of up/down belief) ────────

def _interference_signal(raw_results: list) -> Optional[str]:
    """
    Give the up/down/uncertain verdict a "shape" using the same probe
    outcomes, so analyzer.py can distinguish shutdown-like, censorship-like,
    and tampering-like disruptions instead of lumping everything into a
    generic "disruption":

      ssl_tamper  – TLS handshake failures: classic MITM/interception signature
      blocked     – hosts reachable but returning 4xx/5xx (or a TCP RST) at
                    a rate matching or exceeding clean responses: reachable,
                    filtered at the application layer — a censorship shape
      unreachable – timeouts/errors dominate: consistent with a routing
                    withdrawal or link-down, i.e. a shutdown shape
      None        – no strong shape either way
    """
    if not raw_results:
        return None
    ssl_errs = sum(1 for r in raw_results if r["status"] == "ssl_error")
    blocked  = sum(1 for r in raw_results if r["status"] == "blocked")
    ok       = sum(1 for r in raw_results if r["status"] in ("ok", "open"))
    refused  = sum(1 for r in raw_results if r["status"] == "refused")
    timeouts = sum(1 for r in raw_results if r["status"] == "timeout")
    errors   = sum(1 for r in raw_results if r["status"] == "error")

    if ssl_errs:
        return "ssl_tamper"
    if blocked and blocked >= ok:
        return "blocked"
    if (timeouts + errors) and (timeouts + errors) >= (ok + refused):
        return "unreachable"
    return None


# ── Trinocular adaptive probing loop (per country) ─────────────────────────────

async def _probe_country(info: dict, client: httpx.AsyncClient,
                          prior_belief_up: float, prior_availability: float) -> dict:
    """
    Run one Trinocular probing round for a country: a periodic probe, plus
    adaptive retries while belief stays uncertain, plus extra recovery
    probes if we're checking a country that was previously believed down
    (see trinocular.py and paper §4.3). Belief and availability are carried
    in and returned so the caller can persist them across rounds.
    """
    units = _flatten_targets(info)
    if not units:
        return {
            "disrupted": False, "confirmed_up": False, "confidence": 0,
            "note": "No probe targets configured for this country",
            "interference_signal": None,
            "belief_up": prior_belief_up, "availability": prior_availability,
        }

    block_size   = len(units)
    belief_up    = prior_belief_up
    availability = prior_availability
    prior_state  = trinocular.classify_belief(prior_belief_up)
    min_probes   = (trinocular.recovery_probe_budget(availability)
                     if prior_state == "down" else 1)

    raw_results: list = []
    state = prior_state
    while len(raw_results) < trinocular.MAX_PROBES_PER_ROUND:
        unit = units[len(raw_results) % len(units)]
        r = await _fire_probe(unit, client)
        raw_results.append(r)

        positive = r["status"] in ("ok", "open")
        was_up   = trinocular.classify_belief(belief_up) == "up"
        belief_up = trinocular.bayes_update(belief_up, availability, block_size, positive)
        if was_up:
            # Only learn "normal" availability from steady-state observations —
            # outages shouldn't drag the long-term rate down (§4.4).
            availability = trinocular.update_availability(availability, positive)
        state = trinocular.classify_belief(belief_up)

        if positive and state == "up":
            break                                          # this probe was positive and it's enough -> short-circuit
        if state != "uncertain" and len(raw_results) >= min_probes:
            break                                          # definitive either way, and min_probes was met
        if len(raw_results) < trinocular.MAX_PROBES_PER_ROUND:
            await asyncio.sleep(random.uniform(0.3, 0.8))

    signal     = _interference_signal(raw_results)
    confidence = round(abs(belief_up - 0.5) * 200)   # 0 at belief=0.5, 100 at belief->{0,1}
    statuses   = ", ".join(sorted({r["status"] for r in raw_results}))
    note = (
        f"Trinocular belief P(up)={belief_up:.2f} after {len(raw_results)} "
        f"probe(s) [{statuses}]; state={state}"
    )
    return {
        "disrupted":    state == "down",
        "confirmed_up": state == "up",
        "confidence":   confidence,
        "note":         note,
        "interference_signal": signal,
        "belief_up":    belief_up,
        "availability": availability,
    }


# ── Collector ─────────────────────────────────────────────────────────────────

class ProbeCollector(BaseCollector):
    name = "probe"

    async def collect(self) -> list:
        """Required by base class. Probes never create events — returns []."""
        return []

    async def probe_countries(self, db: Session, country_codes: Optional[set] = None) -> dict:
        """
        Run Trinocular-style probing against configured targets.

        Parameters
        ----------
        db : Session
            Used to load and persist each country's belief state across cycles.
        country_codes : set | None
            Limit to these country codes (default: all configured targets).
            In normal operation this is the set of countries with active events.

        Returns
        -------
        dict  {cc: {disrupted, confirmed_up, confidence, note, interference_signal}}
        """
        targets = {
            cc: info for cc, info in COUNTRY_TARGETS.items()
            if country_codes is None or cc in country_codes
        }
        if not targets:
            return {}

        async with httpx.AsyncClient(
            headers=_HEADERS, timeout=HTTP_TIMEOUT,
            verify=False, follow_redirects=True,
        ) as client:
            # Abort if our own connectivity is broken
            if not await self._anchor_check(client):
                log.warning("[probe] Anchors unreachable — aborting (local connectivity issue)")
                return {}

            results = {}
            for cc, info in targets.items():
                # Jitter between countries to avoid rate-limiting
                await asyncio.sleep(random.uniform(1.0, 3.0))

                row = db.query(CountryBelief).filter(CountryBelief.country_code == cc).first()
                if row is None:
                    row = CountryBelief(country_code=cc,
                                         belief_up=trinocular.INITIAL_BELIEF_UP,
                                         availability=trinocular.INITIAL_AVAILABILITY)
                    db.add(row)

                outcome = await _probe_country(info, client, row.belief_up, row.availability)
                row.belief_up    = outcome["belief_up"]
                row.availability = outcome["availability"]
                row.state        = trinocular.classify_belief(outcome["belief_up"])
                db.commit()

                results[cc] = outcome
                log.debug(f"[probe] {cc}: {outcome}")

        disrupted = sum(1 for r in results.values() if r.get("disrupted"))
        log.info(f"[probe] Checked {len(results)} countries, {disrupted} show disruption")
        return results

    async def _anchor_check(self, client: httpx.AsyncClient) -> bool:
        for a in GLOBAL_ANCHORS:
            r = await _http_probe(a["url"], client)
            if r["status"] in ("ok", "blocked"):
                return True
        return False
