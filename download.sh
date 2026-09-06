#!/bin/bash
set -e
curl https://tabular-api.data.gouv.fr/api/resources/d1e73f98-3740-4579-b452-4d78dd846771/data/json/ > pmrpsh.json
curl https://eu.ftp.opendatasoft.com/sncf/plandata/Export_OpenData_SNCF_GTFS_NewTripId.zip > trains.zip
unzip trains.zip
./txt_to_sqlite.py

