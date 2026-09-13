#!/usr/bin/env python3
"""Core PMR/PSH itinerary-verification logic, shared by the CLI (test.py)
and the JSON API (service.py)."""

from datetime import datetime
from enum import IntEnum
import json
import os
import re
import sqlite3
import unicodedata

HERE = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(HERE, "feed.sqlite")
PMR_PATH = os.path.join(HERE, "pmrpsh.json")

DAY = 24 * 3600

# Correspondances plus courtes que ce seuil (en minutes) déclenchent un avertissement.
MIN_TRANSFER_MINUTES = 30

WEEKDAYS_FR = ["LUNDI", "MARDI", "MERCREDI", "JEUDI", "VENDREDI", "SAMEDI", "DIMANCHE"]


class Verdict(IntEnum):
    OK = 0
    MEH = 1
    KO = 2


verdict_names = {
        0 : "OK",
        1: "MEH",
        2 : "KO",
}


def stop_uic(stop_id):
    """Extract the trailing UIC code from a GTFS stop_id.

    SNCF stop_id look like 'StopArea:OCE87723197' or
    'StopPoint:OCETGV INOUI-87723197'; the UIC code is the trailing digits.
    """
    m = re.search(r"(\d+)$", stop_id or "")
    return m.group(1) if m else None


def normalize_name(s):
    """Fold a station name for case- and accent-insensitive comparison."""
    if not s:
        return ""
    s = unicodedata.normalize("NFKD", s)
    s = "".join(c for c in s if not unicodedata.combining(c))
    s = re.sub("-", " ", s)
    return " ".join(s.casefold().split())


def to_seconds(hms):
    """GTFS times may go past 24:00:00, so compare them as seconds.

    Accepts 'HH:MM:SS' (GTFS) as well as 'HH:MM' (the CLI wall-clock format).
    """
    if not hms:
        return None
    parts = [int(x) for x in hms.split(":")]
    h = parts[0]
    m = parts[1] if len(parts) > 1 else 0
    s = parts[2] if len(parts) > 2 else 0
    return h * 3600 + m * 60 + s


def parse_clock(token):
    """Parse a 'H:M' wall-clock CLI token into a zero-padded 'HH:MM' string."""
    m = re.fullmatch(r"\s*(\d{1,2}):(\d{2})\s*", token or "")
    if not m:
        raise ItineraryError(f"Heure invalide : {token!r} (format attendu HH:MM)")
    h, minutes = int(m.group(1)), int(m.group(2))
    if h > 23 or minutes > 59:
        raise ItineraryError(f"Heure invalide : {token!r} (heure hors plage)")
    return f"{h:02d}:{minutes:02d}"


def parse_hour_ranges(spec):
    """Parse a PMR opening-hours string into a list of (start_s, end_s) tuples.

    Handles several sub-ranges separated by '/' ("07:10 - 13:45 / 15:00 - 18:45")
    and ranges crossing midnight ("05:00 - 01:00").
    """
    if not spec:
        return []
    ranges = []
    for chunk in spec.split("/"):
        chunk = chunk.strip()
        m = re.match(r"^(\d{1,2}):(\d{2})\s*-\s*(\d{1,2}):(\d{2})$", chunk)
        if not m:
            continue
        sh, sm, eh, em = (int(x) for x in m.groups())
        start = sh * 3600 + sm * 60
        end = eh * 3600 + em * 60
        if end <= start:
            end += DAY  # crosses midnight
        ranges.append((start, end))
    return ranges


def within_ranges(ranges, start_s, end_s):
    """True if the whole [start_s, end_s] window fits inside one hour range."""
    if start_s is None:
        start_s = end_s
    if end_s is None:
        end_s = start_s
    if start_s is None:
        return None
    if end_s < start_s:
        end_s += DAY
    for r_start, r_end in ranges:
        for shift in (0, DAY, -DAY):
            if r_start <= start_s + shift and end_s + shift <= r_end:
                return True
    return False


class PmrDirectory:
    """PMR/PSH assistance data indexed by UIC code (from pmrpsh.json)."""

    def __init__(self, path):
        with open(path, encoding="utf-8") as fh:
            records = json.load(fh)
        self.by_uic = {str(r["Code_UIC"]): r for r in records}

        # Les jours fériés sont nationaux : on agrège toutes les dates
        # "JOUR FERIE" publiées par n'importe quelle gare pour repérer un férié
        # même quand la gare courante ne publie pas d'horaires spécifiques.
        self.national_holidays = set()
        for r in records:
            for i in range(1, 21):
                ferie = r.get(f"JOUR FERIE {i}")
                if ferie:
                    self.national_holidays.add(str(ferie).split("T")[0])

    def get(self, uic):
        return self.by_uic.get(str(uic))

    def hours_for_date(self, record, date_str):
        """Return (label, ranges, unavailable_reason, warning) for an AAAAMMJJ date.

        - unavailable_reason set -> assistance not offered that day
        - ranges empty with no reason -> station listed but no published hours
        - warning set -> result is a fallback the caller should flag (e.g. a
          national holiday for which this station publishes no specific hours,
          so the ordinary weekday schedule is used instead)
        """
        day = datetime.strptime(date_str, "%Y%m%d").date()
        weekday_fr = WEEKDAYS_FR[day.weekday()]

        for i in range(1, 8):
            if record.get(f"JOUR INDISPONIBLE {i}") == weekday_fr:
                return (f"{weekday_fr.capitalize()}", [], "jour indisponible", None)

        iso = day.isoformat()
        for i in range(1, 21):
            ferie = record.get(f"JOUR FERIE {i}")
            if ferie and str(ferie).split("T")[0] == iso:
                spec = record.get(f"HORAIRES JOUR FERIE {i}")
                if not spec:
                    return (f"jour férié {iso}", [], "fermé (jour férié)", None)
                return (f"jour férié {iso}", parse_hour_ranges(spec), None, None)

        warning = None
        if iso in self.national_holidays:
            warning = (
                f"{iso} est un jour férié national, mais cette gare ne publie "
                f"pas d'horaires « jour férié » : horaires nominaux du "
                f"{weekday_fr.capitalize()} utilisés par défaut."
            )

        spec = record.get(f"HORAIRES JOUR NOMINAL {weekday_fr}")
        if not spec:
            return (f"{weekday_fr.capitalize()}", [], "pas d'horaires publiés", warning)
        return (f"{weekday_fr.capitalize()}", parse_hour_ranges(spec), None, warning)


class ORM:
    def __init__(self, db):
        self.conn = sqlite3.connect(db)
        self.conn.row_factory = sqlite3.Row

    def get_stop_times_by_trip(self, trip_id):
        cur = self.conn.execute(
            """
            SELECT stop_id, arrival_time, departure_time
            FROM stop_times
            WHERE trip_id = ?
            """,
            (trip_id,),
        )
        return cur.fetchall()

    def iter_stops(self):
        """Every stop row (stop_id + name), for name-based station lookup."""
        return self.conn.execute(
            "SELECT stop_id, stop_name FROM stops"
        ).fetchall()

    def get_stop_by_id(self, stop_id):
        cur = self.conn.execute(
            """
            SELECT stop_id, stop_name, parent_station
            FROM stops
            WHERE stop_id = ?
            """,
            (stop_id,),
        )
        return cur.fetchone()

    def teardown(self):
        self.conn.close()


class ItineraryError(Exception):
    pass


class TripVerifier:
    def __init__(self, db, pmr_path=PMR_PATH):
        self.orm = ORM(db)
        self.pmr = PmrDirectory(pmr_path)

    # -- itinerary -----------------------------------------------------------

    def resolve_station(self, token):
        """Resolve a CLI station token to a set of UIC codes.

        Accepts a bare UIC code (only digits) or a station name matched
        case- and accent-insensitively. A station may be split across
        several stop areas / UIC codes, so every match is returned.
        Returns (uics, label).
        """
        token = (token or "").strip()
        if re.fullmatch(r"\d+", token):
            return {token}, token

        want = normalize_name(token)
        uics = set()
        names = set()
        for row in self.orm.iter_stops():
            if normalize_name(row["stop_name"]) == want:
                uic = stop_uic(row["stop_id"])
                if uic:
                    uics.add(uic)
                    names.add(row["stop_name"])
        if not uics:
            raise ItineraryError(f"Gare inconnue : {token!r}")
        return uics, " / ".join(sorted(names))

    def resolve_stop(self, token):
        """Resolve a CLI station token to a single {name, uic} dict.

        Reuses resolve_station (a bare UIC code, or a station name matched
        case- and accent-insensitively). When a name maps to several stop
        areas / UIC codes, prefer one the PMR/PSH service actually knows about.
        """
        uics, label = self.resolve_station(token)
        chosen = next(
            (u for u in sorted(uics) if self.pmr.get(u) is not None), None
        )
        if chosen is None:
            chosen = sorted(uics)[0]
        return {"name": label, "uic": chosen}

    def search_stations(self, query, limit=20):
        """Autocomplete: station names matching `query` (substring, accent/case
        insensitive), deduplicated, flagged with PMR/PSH eligibility."""
        want = normalize_name(query)
        if not want:
            return []
        seen = {}
        for row in self.orm.iter_stops():
            name = row["stop_name"]
            norm = normalize_name(name)
            if want not in norm:
                continue
            uic = stop_uic(row["stop_id"])
            if not uic:
                continue
            key = norm
            entry = seen.setdefault(
                key,
                {
                    "name": name,
                    "uics": set(),
                    "starts_with": norm.startswith(want),
                },
            )
            entry["uics"].add(uic)

        results = []
        for entry in seen.values():
            eligible = any(self.pmr.get(u) is not None for u in entry["uics"])
            results.append(
                {
                    "name": entry["name"],
                    "uic": sorted(entry["uics"])[0],
                    "eligible": eligible,
                    "starts_with": entry["starts_with"],
                }
            )
        results.sort(key=lambda r: (not r["starts_with"], not r["eligible"], r["name"]))
        return [
            {"name": r["name"], "uic": r["uic"], "eligible": r["eligible"]}
            for r in results[:limit]
        ]

    def build_itinerary(self, day, tokens):
        """Build the visited-station list from a gare-by-gare description.

        `tokens` is the flat CLI list; `day` is only carried through for the
        PMR/PSH report:

            <gare départ> <HH:MM départ>
            (<gare correspondance> <HH:MM arrivée> <HH:MM départ>)*
            <gare arrivée> <HH:MM arrivée>
        """
        if len(tokens) < 4 or (len(tokens) - 4) % 3 != 0:
            raise ItineraryError(
                "Étapes attendues : <date> <gare départ> <HH:MM> "
                "[<gare correspondance> <HH:MM arrivée> <HH:MM départ>]... "
                "<gare arrivée> <HH:MM>"
            )

        visited = [
            {
                "station": self.resolve_stop(tokens[0]),
                "role": "départ",
                "arrival_time": None,
                "departure_time": parse_clock(tokens[1]),
            }
        ]

        for i in range(2, len(tokens) - 2, 3):
            visited.append(
                {
                    "station": self.resolve_stop(tokens[i]),
                    "role": "correspondance",
                    "arrival_time": parse_clock(tokens[i + 1]),
                    "departure_time": parse_clock(tokens[i + 2]),
                }
            )

        visited.append(
            {
                "station": self.resolve_stop(tokens[-2]),
                "role": "arrivée",
                "arrival_time": parse_clock(tokens[-1]),
                "departure_time": None,
            }
        )
        return visited

    def build_report(self, visited, day):
        """Build a render-agnostic report: overall verdict + per-station detail."""
        v = Verdict.OK
        stations = []
        for n, entry in enumerate(visited, start=1):
            st = entry["station"]
            transfer = self._transfer_minutes(entry)
            transfer_warning = (
                transfer is not None and transfer < MIN_TRANSFER_MINUTES
            )
            if transfer_warning:
                v = max(v, Verdict.MEH)
            station_verdict, pmr_lines = self._pmr_report(entry, day)
            v = max(v, station_verdict)
            stations.append(
                {
                    "n": n,
                    "name": st["name"] or "?",
                    "uic": st["uic"],
                    "role": entry["role"],
                    "arrival_time": entry["arrival_time"],
                    "departure_time": entry["departure_time"],
                    "transfer_minutes": transfer,
                    "transfer_warning": transfer_warning,
                    "verdict": station_verdict,
                    "lines": pmr_lines,
                }
            )
        return {"day": day, "verdict": v, "stations": stations}

    @staticmethod
    def _transfer_minutes(entry):
        """Minutes available at a correspondance, or None if not applicable."""
        if entry["role"] != "correspondance":
            return None
        arr = to_seconds(entry["arrival_time"])
        dep = to_seconds(entry["departure_time"])
        if arr is None or dep is None:
            return None
        if dep < arr:
            dep += DAY  # correspondance après minuit
        return (dep - arr) // 60

    def _pmr_report(self, entry, day):
        """Structured lines describing PMR/PSH eligibility for one visited station.

        Each line is {"text": str, "kind": "success"|"info"|"warning"|"error"}
        so the HTML renderer can style it without re-deriving meaning from text.
        """
        st = entry["station"]
        record = self.pmr.get(st["uic"])
        if record is None:
            return (
                Verdict.KO,
                [{"text": "Gare non éligible (absente du service d'assistance)", "kind": "error"}],
            )

        lines = []
        prise = record.get("typePriseEnCharge") or "?"
        tarif = record.get("typeTarification") or "?"
        elig = f"Éligible — prise en charge {prise}, tarification {tarif}"
        if record.get("premierAuDernierTrain"):
            elig += ", du premier au dernier train"
        lines.append({"text": elig, "kind": "success"})

        # Window during which the traveller is physically at the station.
        start_s = to_seconds(entry["arrival_time"]) or to_seconds(entry["departure_time"])
        end_s = to_seconds(entry["departure_time"]) or to_seconds(entry["arrival_time"])
        inrange = Verdict.OK
        label, ranges, reason, warning = self.pmr.hours_for_date(record, day)
        if warning:
            lines.append({"text": warning, "kind": "warning"})
            inrange = Verdict.MEH
        if reason:
            lines.append(
                {"text": f"Horaires assistance ({label}) : {reason} → hors couverture", "kind": "error"}
            )
            inrange = Verdict.KO
        elif not ranges:
            lines.append(
                {"text": f"Horaires assistance ({label}) : non publiés → indéterminé", "kind": "error"}
            )
            inrange = Verdict.KO
        else:
            pretty = " / ".join(
                f"{a // 3600:02d}:{a % 3600 // 60:02d}-{b // 3600 % 24:02d}:{b % 3600 // 60:02d}"
                for a, b in ranges
            )
            ok = within_ranges(ranges, start_s, end_s)
            if not ok:
                inrange = Verdict.KO
            span = (
                entry["arrival_time"] or entry["departure_time"],
                entry["departure_time"] or entry["arrival_time"],
            )
            verdict = "dans la plage" if ok else "HORS PLAGE"
            lines.append(
                {
                    "text": (
                        f"Horaires assistance ({label}) : {pretty} ; "
                        f"présence en gare {span[0]}–{span[1]} → {verdict}"
                    ),
                    "kind": "success" if ok else "error",
                }
            )

        rdv = record.get("lieuRendezVousGare")
        if rdv:
            lines.append({"text": f"Lieu de rendez-vous : {rdv}", "kind": "info"})
        rdv_taxi = record.get("lieuRendezVousTaxi")
        if rdv_taxi:
            lines.append({"text": f"Taxi : {rdv_taxi}", "kind": "info"})
        return (inrange, lines)

    def teardown(self):
        self.orm.teardown()
