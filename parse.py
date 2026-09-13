import re
import test
import sys

def parse_trajets_train(raw_text: str):
    # 1. Extraction de la date (JJ/MM/AAAA -> AAAAMMJJ)
    date_match = re.search(r"\b(\d{2})/(\d{2})/(\d{4})\b", raw_text)
    if date_match:
        day, month, year = date_match.groups()
        date_str = f"{year}{month}{day}"
    else:
        date_str = ""

    # 2. Découpage du texte en blocs de trajets (séparés par les lignes "Durée:")
    trajets_raw = raw_text.split("Durée:")

    # La dernière partie après le dernier "Durée:" ne contient pas de trajet utile
    journey_blocks = trajets_raw[:-1]

    results = []
    time_regex = re.compile(r"^\d{1,2}:\d{2}$")

    # Mots/lignes parasites à ignorer
    ignore_lines = {
        "correspondance",
        "2e classe",
        "1re classe",
        "espace vélo gratuit",
        "se connecter",
        "créer un compte",
        "rechercher",
        "retour",
        "age",
    }

    for block in journey_blocks:
        lines = [line.strip() for line in block.split("\n") if line.strip()]

        # On filtre pour ne garder que les horaires et les gares
        clean_elements = []
        for line in lines:
            if line.lower() in ignore_lines or "sélectionnez" in line.lower():
                continue
            if "➜" in line or "adulte" in line.lower() or "1.2.train" in line.lower():
                continue
            clean_elements.append(line)

        # On cherche le premier horaire pour démarrer le trajet dans le bloc
        start_idx = None
        for i, elem in enumerate(clean_elements):
            if time_regex.match(elem):
                start_idx = i
                break

        if start_idx is None:
            continue

        journey_elements = clean_elements[start_idx:]

        # Chaque segment (train) est composé de 4 éléments :
        # [heure_dep, gare_dep, heure_arr, gare_arr]
        legs = []
        for i in range(0, len(journey_elements) - 3, 4):
            dep_time = journey_elements[i]
            dep_station = journey_elements[i + 1]
            arr_time = journey_elements[i + 2]
            arr_station = journey_elements[i + 3]

            # Vérification de sécurité sur le format des heures
            if time_regex.match(dep_time) and time_regex.match(arr_time):
                legs.append(
                    {
                        "dep_time": dep_time,
                        "dep_station": dep_station,
                        "arr_time": arr_time,
                        "arr_station": arr_station,
                    }
                )

        if not legs:
            continue

        # 3. Construction de la liste finale pour ce trajet
        current_journey = [date_str]

        # Gare et heure de départ initiale
        current_journey.extend([legs[0]["dep_station"], legs[0]["dep_time"]])

        # Correspondances intermédiaires
        for i in range(len(legs) - 1):
            current_journey.append(legs[i]["arr_station"])
            current_journey.append(legs[i]["arr_time"])
            current_journey.append(legs[i + 1]["dep_time"])

        # Gare et heure d'arrivée finale
        current_journey.extend([legs[-1]["arr_station"], legs[-1]["arr_time"]])

        results.append(current_journey)

    return results

raw_data = sys.stdin.read()

trajets = parse_trajets_train(raw_data)

for trip in trajets:
    print(trip)
    #test.verify(trip)

