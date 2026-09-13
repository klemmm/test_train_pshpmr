#!/usr/bin/env python3
"""Parses a raw text paste of a "1.2.TRAIN" search-results page into a list of
candidate journeys (departure/correspondance/arrivée steps), for the "coller
une page" import path of the PMR/PSH itinerary checker.

The site renders one result per "carte trajet", and copy-pasting the page
gives a flat text dump where every visible label ends up on its own line:
times, station names, "Correspondance" markers, fare-class labels, a
"Durée: ..." summary per option, plus assorted nav/footer/passenger-picker
noise. There is no structural markup left once it's plain text, so this is
necessarily heuristic — the design goal is to degrade gracefully (skip a
malformed option, keep the rest) rather than to raise on the first surprise.
"""

import re
import unicodedata
from datetime import date

TIME_RE = re.compile(r"^([0-1]?\d|2[0-3]):[0-5]\d$")
NUMERIC_DATE_RE = re.compile(r"\b(\d{2})/(\d{2})/(\d{4})\b")

MONTHS_FR = {
    "janvier": 1, "fevrier": 2, "mars": 3, "avril": 4, "mai": 5, "juin": 6,
    "juillet": 7, "aout": 8, "septembre": 9, "octobre": 10, "novembre": 11,
    "decembre": 12,
}
MONTH_NAMES = "|".join(MONTHS_FR)
HEADER_DATE_RE = re.compile(
    r"\ble\s+[a-z]+\s+(\d{1,2})\s+(" + MONTH_NAMES + r")\s+(\d{4})\b"
)

# Lines that are noise regardless of position, matched case/accent-insensitively.
NOISE_EXACT = {
    "1.2.train", "se connecter", "creer un compte", "rechercher", "retour",
    "age", "correspondance", "direct", "directs uniquement",
    "espace velo gratuit", "equipements", "voir le detail", "voir les details",
    "modifier", "filtrer", "trier par", "prix croissant", "prix decroissant",
    "duree croissante", "duree decroissante", "depart le plus tot",
    "depart le plus tard", "arrivee la plus tot", "arrivee la plus tard",
    "support", "a propos", "cgv", "mentions legales", "accueil", "mon compte",
    "wifi gratuit", "bar", "prise electrique", "climatisation",
}
NOISE_PREFIXES = ("selectionnez", "adulte", "enfant", "senior", "jeune", "bebe")
NOISE_PATTERNS = [
    re.compile(r"^\d\s*(er|re|ere|e|eme)\s*classe$"),
    re.compile(r"^\d+[.,]\d{2}\s*€"),
    re.compile(r"€\s*$"),
    re.compile(r"\(\d{1,3}\s*-\s*\d{1,3}\s*ans\)"),
    re.compile(r"^\d+\s*h\s*\d{0,2}\s*(en\s|$)"),  # bare "5h24" / "5h24 en ..." leftovers
]


def _fold(s):
    """Case/accent-insensitive fold, kept close to pmr_core.normalize_name
    (but without collapsing hyphens: station names still need them intact
    for display, this helper is only used to classify a line as noise)."""
    s = unicodedata.normalize("NFKD", s or "")
    s = "".join(c for c in s if not unicodedata.combining(c))
    return " ".join(s.casefold().split())


def _is_noise(line, folded):
    if folded in NOISE_EXACT:
        return True
    if any(folded.startswith(p) for p in NOISE_PREFIXES):
        return True
    if "➜" in line:
        return True
    if any(p.search(folded) for p in NOISE_PATTERNS):
        return True
    if len(folded) > 90:  # stray paragraph text, never a station/time line
        return True
    return False


def _numeric_date(text):
    for m in NUMERIC_DATE_RE.finditer(text):
        day, month, year = (int(g) for g in m.groups())
        try:
            return date(year, month, day)
        except ValueError:
            continue
    return None


def _header_date(folded_line):
    m = HEADER_DATE_RE.search(folded_line)
    if not m:
        return None
    day, month_name, year = m.groups()
    try:
        return date(int(year), MONTHS_FR[month_name], int(day))
    except ValueError:
        return None


def _to_ymd(d):
    return f"{d.year:04d}{d.month:02d}{d.day:02d}" if d else None


def _finalize_journey(buffer, current_date, direction, duration_text):
    """Turn a flat buffer of alternating time/station lines into a journey
    dict, or None if it doesn't contain at least one full leg."""
    start_idx = next((i for i, tok in enumerate(buffer) if TIME_RE.match(tok)), None)
    if start_idx is None:
        return None
    tokens = buffer[start_idx:]

    legs = []
    i = 0
    while i + 3 < len(tokens):
        dep_time, dep_station, arr_time, arr_station = tokens[i:i + 4]
        if TIME_RE.match(dep_time) and not TIME_RE.match(dep_station) \
                and TIME_RE.match(arr_time) and not TIME_RE.match(arr_station):
            legs.append((dep_time, dep_station, arr_time, arr_station))
            i += 4
        else:
            # Misaligned tokens (unrecognised noise line slipped through):
            # resync on the next plausible departure-time position instead
            # of aborting the whole journey.
            i += 1

    if not legs:
        return None

    steps = [{"role": "départ", "station": legs[0][1], "arr": "", "dep": legs[0][0]}]
    for j in range(len(legs) - 1):
        steps.append({
            "role": "correspondance",
            "station": legs[j][3],
            "arr": legs[j][2],
            "dep": legs[j + 1][0],
        })
    steps.append({
        "role": "arrivée",
        "station": legs[-1][3],
        "arr": legs[-1][2],
        "dep": "",
    })

    return {
        "date": _to_ymd(current_date),
        "direction": direction,
        "duration_text": duration_text,
        "from": steps[0]["station"],
        "to": steps[-1]["station"],
        "departure": steps[0]["dep"],
        "arrival": steps[-1]["arr"],
        "transfers": len(steps) - 2,
        "steps": steps,
    }


def parse_pasted_page(raw_text):
    """Parse a raw copy-paste of a 1.2.TRAIN results page into a list of
    journey options, each already shaped like the frontend's `steps` array
    (role/station/arr/dep), plus display metadata (date, direction, duration,
    summary fields). Never raises: unparseable input just yields []."""
    if not raw_text or not raw_text.strip():
        return []

    text = unicodedata.normalize("NFKC", raw_text).replace("\xa0", " ")
    lines = [ln.strip() for ln in text.splitlines()]
    lines = [ln for ln in lines if ln]

    fallback_date = _numeric_date(text)
    current_date = fallback_date
    direction = "aller"

    results = []
    buffer = []
    duration_text = None
    awaiting_new_journey = False

    def flush():
        journey = _finalize_journey(buffer, current_date, direction, duration_text)
        if journey:
            results.append(journey)

    for line in lines:
        folded = _fold(line)

        header_date = _header_date(folded)
        if header_date:
            current_date = header_date

        if folded.startswith("selectionnez votre trajet retour"):
            direction = "retour"
            continue
        if folded.startswith("selectionnez votre trajet aller"):
            direction = "aller"
            continue

        if folded.startswith("duree") or folded.startswith("durée"):
            flush()
            buffer = []
            duration_text = re.sub(r"(?i)^dur[ée]e\s*:?\s*", "", line).strip() or None
            awaiting_new_journey = True
            continue

        if _is_noise(line, folded):
            continue

        if awaiting_new_journey:
            if not TIME_RE.match(line):
                # still trailing metadata (class labels etc. that weren't caught
                # by the noise list) between "Durée:" and the next journey
                continue
            awaiting_new_journey = False

        buffer.append(line)

    flush()
    return results
