#!/usr/bin/env python3
"""CLI entry point for the PMR/PSH itinerary checker.

Core logic lives in pmr_core.py (shared with service.py, the JSON API) and
HTML rendering lives in report_html.py.
"""

import argparse
import sys

from pmr_core import DB_PATH, ItineraryError, TripVerifier, verdict_names
from report_html import default_report_path, render_html_report


def display_itinerary(tv, visited, day, output_path=None):
    report = tv.build_report(visited, day)
    path = output_path or default_report_path(day)
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(render_html_report(report))
    print(f"Rapport PMR/PSH généré : {path}")
    print(f"Verdict global : {verdict_names[report['verdict']]}")
    return path


def main():
    parser = argparse.ArgumentParser(
        description=(
            "Vérifie l'éligibilité à l'assistance PMR/PSH le long d'un itinéraire "
            "ferroviaire décrit gare par gare avec les horaires de passage "
            "(départ, correspondances, arrivée)."
        )
    )
    parser.add_argument("date", help="date de circulation, format AAAAMMJJ")
    parser.add_argument(
        "etapes",
        nargs="+",
        metavar="étape",
        help=(
            "gare de départ puis heure de départ (HH:MM) ; "
            "pour chaque correspondance : gare, heure d'arrivée, heure de départ ; "
            "enfin gare d'arrivée puis heure d'arrivée. Une gare est un code UIC "
            "ou un nom (insensible à la casse et aux accents)."
        ),
    )
    parser.add_argument(
        "-o", "--output",
        metavar="FICHIER",
        help="chemin du rapport HTML à générer (par défaut : itineraire_pmr_<date>.html)",
    )
    args = parser.parse_args()

    tv = TripVerifier(DB_PATH)
    try:
        visited = tv.build_itinerary(args.date, args.etapes)
        display_itinerary(tv, visited, args.date, args.output)
    except ItineraryError as exc:
        print(exc)
        tv.teardown()
        sys.exit(1)
    tv.teardown()


def verify(trip):
    tv = TripVerifier(DB_PATH)
    visited = tv.build_itinerary(trip[0], trip[1:])
    display_itinerary(tv, visited, trip[0])
    tv.teardown()


if __name__ == "__main__":
    main()
