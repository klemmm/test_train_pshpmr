#!/usr/bin/env python3
"""JSON API + static front-end for the PMR/PSH itinerary checker.

GET  /api/stations?q=<text>       -> autocomplete station search
POST /api/verify   {date, etapes} -> same shape test.py's CLI takes:
                                      date: "AAAAMMJJ"
                                      etapes: [gare, "HH:MM", ...] flat list
                                      (départ ; [gare, arrivée, départ]* ; arrivée)
POST /api/parse-paste {text}      -> parses a raw copy-paste of a 1.2.TRAIN
                                      results page into candidate journeys
"""

import os

from flask import Flask, jsonify, request, send_from_directory

from journey_parser import parse_pasted_page
from pmr_core import DB_PATH, ItineraryError, TripVerifier, Verdict

HERE = os.path.dirname(os.path.abspath(__file__))
STATIC_DIR = os.path.join(HERE, "static")

app = Flask(__name__, static_folder=STATIC_DIR, static_url_path="")


def get_verifier():
    return TripVerifier(DB_PATH)


def serialize_verdict(v):
    return {"code": int(v), "name": v.name}


def serialize_report(report):
    return {
        "day": report["day"],
        "verdict": serialize_verdict(report["verdict"]),
        "stations": [
            {**station, "verdict": serialize_verdict(station["verdict"])}
            for station in report["stations"]
        ],
    }


@app.get("/api/stations")
def api_stations():
    query = request.args.get("q", "")
    limit = min(int(request.args.get("limit", 20)), 50)
    tv = get_verifier()
    try:
        results = tv.search_stations(query, limit=limit)
    finally:
        tv.teardown()
    return jsonify({"results": results})


@app.post("/api/verify")
def api_verify():
    payload = request.get_json(silent=True) or {}
    date = (payload.get("date") or "").strip()
    etapes = payload.get("etapes") or []

    if not date:
        return jsonify({"error": "Date manquante (format attendu AAAAMMJJ)."}), 400
    if not isinstance(etapes, list) or not etapes:
        return jsonify({"error": "Étapes manquantes."}), 400

    tv = get_verifier()
    try:
        visited = tv.build_itinerary(date, [str(e) for e in etapes])
        report = tv.build_report(visited, date)
        return jsonify(serialize_report(report))
    except ItineraryError as exc:
        return jsonify({"error": str(exc)}), 400
    except ValueError:
        return jsonify({"error": "Date invalide (format attendu AAAAMMJJ)."}), 400
    finally:
        tv.teardown()


def _etapes_from_journey(journey):
    etapes = []
    for step in journey["steps"]:
        if step["role"] == "départ":
            etapes.extend([step["station"], step["dep"]])
        elif step["role"] == "arrivée":
            etapes.extend([step["station"], step["arr"]])
        else:
            etapes.extend([step["station"], step["arr"], step["dep"]])
    return etapes


def _verify_journey(tv, journey):
    """Verify one parsed journey, returning {"report": ...} or {"error": ...}.

    Never raises: a malformed or unresolvable journey just yields an error
    string so one bad option doesn't stop the rest of the batch from being
    verified.
    """
    if not journey.get("date"):
        return {"error": "Date inconnue : impossible de vérifier ce trajet."}
    try:
        visited = tv.build_itinerary(journey["date"], _etapes_from_journey(journey))
        report = tv.build_report(visited, journey["date"])
        return {"report": serialize_report(report)}
    except ItineraryError as exc:
        return {"error": str(exc)}
    except ValueError:
        return {"error": "Date ou horaire invalide."}


@app.post("/api/parse-paste")
def api_parse_paste():
    payload = request.get_json(silent=True) or {}
    text = payload.get("text") or ""
    if not text.strip():
        return jsonify({"error": "Texte vide : collez le contenu d'une page 1.2.TRAIN."}), 400

    journeys = parse_pasted_page(text)
    if not journeys:
        return jsonify({
            "error": "Aucun trajet reconnu dans ce texte. Vérifiez qu'il s'agit bien "
                     "d'un copier-coller d'une page de résultats 1.2.TRAIN.",
        }), 400

    tv = get_verifier()
    try:
        resolved_cache = {}

        def is_known(name):
            if name not in resolved_cache:
                try:
                    tv.resolve_station(name)
                    resolved_cache[name] = True
                except ItineraryError:
                    resolved_cache[name] = False
            return resolved_cache[name]

        for journey in journeys:
            for step in journey["steps"]:
                step["resolved"] = is_known(step["station"])
            journey.update(_verify_journey(tv, journey))
    finally:
        tv.teardown()

    return jsonify({"journeys": journeys})


@app.get("/")
def index():
    return send_from_directory(STATIC_DIR, "index.html")


if __name__ == "__main__":
    app.run(host="127.0.0.1", port=5000, debug=True)
