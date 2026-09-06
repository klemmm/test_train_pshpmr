#!/usr/bin/env python3

from datetime import datetime
from enum import IntEnum
import argparse
import json
import os
import re
import sys
import sqlite3
import unicodedata

HERE = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(HERE, "feed.sqlite")
PMR_PATH = os.path.join(HERE, "pmrpsh.json")

DAY = 24 * 3600

# Correspondances plus courtes que ce seuil (en minutes) déclenchent un avertissement.
MIN_TRANSFER_MINUTES = 30

WEEKDAYS_FR = ["LUNDI", "MARDI", "MERCREDI", "JEUDI", "VENDREDI", "SAMEDI", "DIMANCHE"]

class ServiceException(IntEnum):
    RUNS = 1
    DOES_NOT_RUN = 2


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
    return " ".join(s.casefold().split())


def to_seconds(hms):
    """GTFS times may go past 24:00:00, so compare them as seconds."""
    if not hms:
        return None
    h, m, s = (int(x) for x in hms.split(":"))
    return h * 3600 + m * 60 + s


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

    def get(self, uic):
        return self.by_uic.get(str(uic))

    @staticmethod
    def hours_for_date(record, date_str):
        """Return (label, ranges, unavailable_reason) for the given AAAAMMJJ date.

        - unavailable_reason set -> assistance not offered that day
        - ranges empty with no reason -> station listed but no published hours
        """
        day = datetime.strptime(date_str, "%Y%m%d").date()
        weekday_fr = WEEKDAYS_FR[day.weekday()]

        for i in range(1, 8):
            if record.get(f"JOUR INDISPONIBLE {i}") == weekday_fr:
                return (f"{weekday_fr.capitalize()}", [], "jour indisponible")

        iso = day.isoformat()
        for i in range(1, 21):
            ferie = record.get(f"JOUR FERIE {i}")
            if ferie and str(ferie).split("T")[0] == iso:
                spec = record.get(f"HORAIRES JOUR FERIE {i}")
                if not spec:
                    return (f"jour férié {iso}", [], "fermé (jour férié)")
                return (f"jour férié {iso}", parse_hour_ranges(spec), None)

        spec = record.get(f"HORAIRES JOUR NOMINAL {weekday_fr}")
        if not spec:
            return (f"{weekday_fr.capitalize()}", [], "pas d'horaires publiés")
        return (f"{weekday_fr.capitalize()}", parse_hour_ranges(spec), None)


class ORM:
    def __init__(self, db):
        self.conn = sqlite3.connect(db)
        self.conn.row_factory = sqlite3.Row


    def get_trips_by_headsign(self, headsign):
        cur = self.conn.execute(
            """
            SELECT route_id, service_id, trip_id, trip_headsign,
                   direction_id, block_id, shape_id
            FROM trips
            WHERE trip_headsign = ?
            """,
            (headsign,),
        )
        return cur.fetchall()

    def count_running_by_service_and_date(self, service_id, day):
            cur_calendar = self.conn.execute(
                """
                SELECT COUNT(*)
                FROM calendar_dates
                WHERE exception_type = ? AND service_id = ? AND date = ?
                """,
                (ServiceException.RUNS, service_id, day),
            )
            (count, ) = cur_calendar.fetchone()
            return count

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

    def get_ordered_stops(self, trip_id):
        """Ordered list of stops for a trip, with station name and UIC code."""
        cur = self.conn.execute(
            """
            SELECT st.stop_id, st.arrival_time, st.departure_time,
                   st.stop_sequence, s.stop_name
            FROM stop_times st
            LEFT JOIN stops s ON s.stop_id = st.stop_id
            WHERE st.trip_id = ?
            ORDER BY CAST(st.stop_sequence AS INTEGER)
            """,
            (trip_id,),
        )
        stops = []
        for row in cur.fetchall():
            stops.append(
                {
                    "stop_id": row["stop_id"],
                    "uic": stop_uic(row["stop_id"]),
                    "name": row["stop_name"],
                    "seq": row["stop_sequence"],
                    "arrival_time": row["arrival_time"],
                    "departure_time": row["departure_time"],
                }
            )
        return stops

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

    # -- circulation check ------------------------------------------------

    def resolve_trip(self, headsign, day):
        """Return the trip matching a train number that actually runs on `day`.

        Mirrors the old process_train_number logic but returns the trip row so
        the itinerary builder can reuse it.
        """
        exists = False
        for trip in self.orm.get_trips_by_headsign(headsign):
            exists = True
            count = self.orm.count_running_by_service_and_date(trip["service_id"], day)
            if count > 0:
                print(
                    f"Le train {headsign} circule bien le {day}, "
                    f"avec le service {trip['service_id']}"
                )
                return trip
        if exists:
            raise ItineraryError(f"Le train {headsign} ne circule pas le {day}")
        raise ItineraryError(f"Le train {headsign} n'existe pas.")

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

    def _find_index(self, stops, uics, after=-1):
        for i in range(after + 1, len(stops)):
            if stops[i]["uic"] in uics:
                return i
        return None

    def _solve(self, trains_stops, k, board_idx, destination):
        """Recursively pick transfer stations for trains k..last.

        Returns a list of segments (stops, board_idx, alight_idx), one per
        train, or None if no feasible chain exists from this state.

        Heuristic: stay on the current train as long as possible, i.e. try the
        latest feasible common station first, and backtrack if the rest of the
        chain cannot be completed.
        """
        cur = trains_stops[k]

        if k == len(trains_stops) - 1:
            alight = self._find_index(cur, destination, after=board_idx)
            if alight is None:
                return None
            return [(cur, board_idx, alight)]

        nxt = trains_stops[k + 1]
        nxt_index = {}
        for j, s in enumerate(nxt):
            nxt_index.setdefault(s["uic"], j)

        candidates = []
        for i in range(board_idx + 1, len(cur)):
            uic = cur[i]["uic"]
            j = nxt_index.get(uic)
            if j is None:
                continue
            arr = to_seconds(cur[i]["arrival_time"] or cur[i]["departure_time"])
            dep = to_seconds(nxt[j]["departure_time"] or nxt[j]["arrival_time"])
            if arr is not None and dep is not None and dep < arr:
                continue  # train k+1 leaves before train k arrives
            candidates.append((i, j))

        # latest station on the current train first
        for i, j in sorted(candidates, key=lambda c: c[0], reverse=True):
            rest = self._solve(trains_stops, k + 1, j, destination)
            if rest is not None:
                return [(cur, board_idx, i)] + rest
        return None

    def build_itinerary(self, day, origin, destination, train_numbers):
        origin_uics, origin_label = self.resolve_station(origin)
        destination_uics, destination_label = self.resolve_station(destination)

        trains_stops = []
        for headsign in train_numbers:
            trip = self.resolve_trip(headsign, day)
            stops = self.orm.get_ordered_stops(trip["trip_id"])
            trains_stops.append(stops)

        board_idx = self._find_index(trains_stops[0], origin_uics)
        if board_idx is None:
            raise ItineraryError(
                f"Le train {train_numbers[0]} ne dessert pas la gare de départ {origin_label}"
            )

        segments = self._solve(trains_stops, 0, board_idx, destination_uics)
        if segments is None:
            raise ItineraryError(
                "Impossible de relier "
                f"{origin_label} à {destination_label} avec les trains "
                f"{', '.join(train_numbers)} le {day} "
                "(pas de correspondance commune ou temps insuffisant)."
            )

        # Visited stations: boarding stop of train 1, then every alighting stop.
        visited = []
        first_stops, first_board, _ = segments[0]
        visited.append(
            {
                "station": first_stops[first_board],
                "role": "départ",
                "arrive_par": None,
                "repart_par": train_numbers[0],
                "arrival_time": None,
                "departure_time": first_stops[first_board]["departure_time"],
            }
        )
        for idx, (stops, _, alight) in enumerate(segments):
            arrived_by = train_numbers[idx]
            leaves_by = train_numbers[idx + 1] if idx + 1 < len(train_numbers) else None
            role = "arrivée" if leaves_by is None else "correspondance"
            entry = {
                "station": stops[alight],
                "role": role,
                "arrive_par": arrived_by,
                "repart_par": leaves_by,
                "arrival_time": stops[alight]["arrival_time"],
                "departure_time": None,
            }
            if leaves_by is not None:
                nxt_stops, nxt_board, _ = segments[idx + 1]
                entry["departure_time"] = nxt_stops[nxt_board]["departure_time"]
            visited.append(entry)

        return visited

    def display_itinerary(self, visited, day):
        print()
        print("Gares visitées :")
        for n, entry in enumerate(visited, start=1):
            st = entry["station"]
            name = st["name"] or "?"
            bits = [f"{n}. {name} (UIC {st['uic']}) [{entry['role']}]"]
            if entry["arrive_par"]:
                bits.append(
                    f"arrivée {entry['arrival_time']} par le train {entry['arrive_par']}"
                )
            if entry["repart_par"]:
                bits.append(
                    f"départ {entry['departure_time']} par le train {entry['repart_par']}"
                )
            print("   " + " | ".join(bits))
            transfer = self._transfer_minutes(entry)
            if transfer is not None and transfer < MIN_TRANSFER_MINUTES:
                print(
                    f"      ⚠ Correspondance courte : {transfer} min "
                    f"(< {MIN_TRANSFER_MINUTES} min) entre le train "
                    f"{entry['arrive_par']} et le train {entry['repart_par']}"
                )
            ok, pmr_lines = self._pmr_report(entry, day)
            for line in pmr_lines:
                print("      " + line)
            print(f"\nVerdict: {ok}")

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
        """Lines describing PMR/PSH eligibility for one visited station."""
        st = entry["station"]
        record = self.pmr.get(st["uic"])
        if record is None:
            return ("KO", ["PMR/PSH : gare non éligible (absente du service d'assistance)"])

        lines = []
        prise = record.get("typePriseEnCharge") or "?"
        tarif = record.get("typeTarification") or "?"
        elig = f"PMR/PSH : éligible — prise en charge {prise}, tarification {tarif}"
        if record.get("premierAuDernierTrain"):
            elig += ", du premier au dernier train"
        lines.append(elig)

        # Window during which the traveller is physically at the station.
        start_s = to_seconds(entry["arrival_time"]) or to_seconds(entry["departure_time"])
        end_s = to_seconds(entry["departure_time"]) or to_seconds(entry["arrival_time"])

        inrange = "OK"
        label, ranges, reason = PmrDirectory.hours_for_date(record, day)
        if reason:
            lines.append(f"Horaires assistance ({label}) : {reason} → hors couverture")
        elif not ranges:
            lines.append(f"Horaires assistance ({label}) : non publiés → indéterminé")
        else:
            pretty = " / ".join(
                f"{a // 3600:02d}:{a % 3600 // 60:02d}-{b // 3600 % 24:02d}:{b % 3600 // 60:02d}"
                for a, b in ranges
            )
            ok = within_ranges(ranges, start_s, end_s)
            if not ok:
                inrange = "KO"
            span = (
                entry["arrival_time"] or entry["departure_time"],
                entry["departure_time"] or entry["arrival_time"],
            )
            verdict = "dans la plage" if ok else "HORS PLAGE"
            lines.append(
                f"Horaires assistance ({label}) : {pretty} ; "
                f"présence en gare {span[0]}–{span[1]} → {verdict}"
            )

        rdv = record.get("lieuRendezVousGare")
        if rdv:
            lines.append(f"Lieu : {rdv}")
        rdv_taxi = record.get("lieuRendezVousTaxi")
        if rdv_taxi:
            lines.append(f"Taxi : {rdv_taxi}")
        return (inrange, lines)

    def teardown(self):
        self.orm.teardown()


def main():
    parser = argparse.ArgumentParser(
        description=(
            "Vérifie un itinéraire ferroviaire et liste les gares visitées "
            "(départ, correspondances, arrivée). Les gares de correspondance "
            "sont déduites automatiquement des sillons des trains à la date donnée."
        )
    )
    parser.add_argument("date", help="date de circulation, format AAAAMMJJ")
    parser.add_argument(
        "origin",
        help="gare de départ : code UIC ou nom (insensible à la casse et aux accents)",
    )
    parser.add_argument(
        "destination",
        help="gare d'arrivée : code UIC ou nom (insensible à la casse et aux accents)",
    )
    parser.add_argument(
        "trains",
        nargs="+",
        metavar="train",
        help="numéros de train dans l'ordre du voyage",
    )
    args = parser.parse_args()

    tv = TripVerifier(DB_PATH)
    try:
        visited = tv.build_itinerary(
            args.date, args.origin, args.destination, args.trains
        )
        tv.display_itinerary(visited, args.date)
    except ItineraryError as exc:
        print(exc)
        tv.teardown()
        sys.exit(1)
    tv.teardown()


if __name__ == "__main__":
    main()
