"""Download the free public sources and write the committed snapshot.

The default pipeline never calls the network. This script is the reproducible
way to refresh ``data/snapshot`` and ``data/manifest.json``. Pass
``--cache-dir`` to reuse files already downloaded in this environment.
"""

from __future__ import annotations

import argparse
import csv
import gzip
import hashlib
import json
import os
import sys
import urllib.request
from pathlib import Path

import duckdb
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
SNAPSHOT = ROOT / "data" / "snapshot"
CACHE = Path(os.environ.get("SNAPSHOT_CACHE", "/tmp/road-data"))

ARDD_URL = (
    "https://aueprod01ckanstg.blob.core.windows.net/public-catalogue/"
    "0faa594e-b846-45d9-9fdf-d5f1dfa27628/bitre_fatalities_sep2026.xlsx"
)
ERP_URL = (
    "https://data.api.abs.gov.au/rest/data/ERP_Q/all"
    "?startPeriod=2017-Q4&endPeriod=2026-Q3&format=csv"
)
SA4_URL = (
    "https://data.api.abs.gov.au/rest/data/ABS_ANNUAL_ERP_ASGS2021/ERP.SA4..A"
    "?startPeriod=2018&endPeriod=2025"
)
VIC_URL = (
    "https://opendata.transport.vic.gov.au/dataset/bb77800e-1857-4edc-bf9e-e188437a1c8e/"
    "resource/5df1f373-0c90-48f5-80e1-7b2a35507134/download/victorian_road_crash_data.csv"
)
MAP_URL = (
    "https://raw.githubusercontent.com/nvkelso/natural-earth-vector/master/geojson/"
    "ne_50m_admin_1_states_provinces.geojson"
)
NRSS_URL = "https://www.roadsafety.gov.au/nrss/fact-sheets/performance-monitoring"

ACCESS_DATE = os.environ.get("SNAPSHOT_ACCESS_DATE", "2026-11-11")

STATE_CODE = {
    "New South Wales": "NSW",
    "Victoria": "VIC",
    "Queensland": "QLD",
    "South Australia": "SA",
    "Western Australia": "WA",
    "Tasmania": "TAS",
    "Northern Territory": "NT",
    "Australian Capital Territory": "ACT",
}


def _download(url: str, dest: Path, headers: dict | None = None) -> None:
    dest.parent.mkdir(parents=True, exist_ok=True)
    if dest.exists() and dest.stat().st_size > 0:
        print(f"cache {dest.name}")
        return
    print(f"GET {url}")
    req = urllib.request.Request(url, headers=headers or {"User-Agent": "road-toll-reversal/1.0"})
    with urllib.request.urlopen(req, timeout=180) as response, dest.open("wb") as handle:
        while True:
            chunk = response.read(1024 * 1024)
            if not chunk:
                break
            handle.write(chunk)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _rows_gz(path: Path) -> int:
    with gzip.open(path, "rt", newline="") as handle:
        return max(sum(1 for _ in handle) - 1, 0)


def _write_fatalities(xlsx: Path, dest: Path) -> int:
    frame = pd.read_excel(xlsx, sheet_name="BITRE_Fatality", header=4)
    frame = frame.rename(
        columns={
            "Crash ID": "crash_id",
            "State": "state",
            "Month": "month",
            "Year": "year",
            "Dayweek": "day_of_week",
            "Time": "time",
            "Crash Type": "crash_type",
            "Bus Involvement": "bus_involvement",
            "Heavy Rigid Truck Involvement": "heavy_rigid_truck",
            "Articulated Truck Involvement": "articulated_truck",
            "Speed Limit": "speed_limit",
            "Road User": "road_user",
            "Gender": "gender",
            "Age": "age",
            "National Remoteness Areas 2021": "remoteness",
            "SA4 Name 2021": "sa4_name",
            "National LGA Name 2021": "lga_name",
            "National Road Type": "road_type",
            "Christmas Period": "christmas_period",
            "Easter Period": "easter_period",
        }
    )
    frame = frame.dropna(subset=["state", "year", "month"])
    frame["crash_id"] = frame["crash_id"].astype(str).str.replace(r"\.0$", "", regex=True)
    frame["time"] = frame["time"].apply(lambda value: "" if pd.isna(value) else str(value)[:8])
    dest.parent.mkdir(parents=True, exist_ok=True)
    frame.to_csv(dest, index=False, compression="gzip")
    return len(frame)


def _write_erp(source: Path, dest: Path) -> int:
    dest.parent.mkdir(parents=True, exist_ok=True)
    duckdb.sql(
        f"""
        COPY (
          SELECT
            REGION AS region_code,
            SEX AS sex_code,
            AGE AS age,
            TIME_PERIOD AS time_period,
            CAST(OBS_VALUE AS DOUBLE) AS population
          FROM read_csv('{source.as_posix()}', header = true, all_varchar = true)
          WHERE MEASURE = '1'
            AND (
              (SEX = '3' AND (AGE = 'TOT' OR (regexp_full_match(AGE, '[0-9]{{1,3}}') AND try_cast(AGE AS INTEGER) <= 120)))
              OR (SEX IN ('1', '2') AND AGE = 'TOT')
            )
          ORDER BY time_period, region_code, sex_code, try_cast(age AS INTEGER) NULLS LAST
        ) TO '{dest.as_posix()}' (FORMAT CSV, HEADER true, COMPRESSION gzip);
        """
    )
    return _rows_gz(dest)


def _write_sa4(source: Path, dest: Path) -> int:
    rows = []
    with source.open(newline="") as handle:
        reader = csv.reader(handle)
        next(reader)
        for row in reader:
            if len(row) < 15 or row[6] != "SA4":
                continue
            rows.append(
                {
                    "sa4_code": row[8],
                    "sa4_name": row[9],
                    "year": int(row[12]),
                    "population": int(float(row[14])),
                }
            )
    dest.parent.mkdir(parents=True, exist_ok=True)
    with gzip.open(dest, "wt", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=["sa4_code", "sa4_name", "year", "population"])
        writer.writeheader()
        writer.writerows(rows)
    return len(rows)


def _write_vic(source: Path, dest: Path) -> int:
    columns = [
        "ACCIDENT_NO",
        "ACCIDENT_DATE",
        "ACCIDENT_TIME",
        "ACCIDENT_TYPE",
        "DAY_OF_WEEK",
        "LIGHT_CONDITION",
        "ROAD_GEOMETRY",
        "SEVERITY",
        "SPEED_ZONE",
        "RUN_OFFROAD",
        "ROAD_TYPE",
        "LGA_NAME",
        "DTP_REGION",
        "FATALITY",
        "SERIOUSINJURY",
        "PEDESTRIAN",
        "BICYCLIST",
        "MOTORCYCLIST",
        "PILLION",
        "DRIVER",
        "PASSENGER",
        "YOUNG_DRIVER_18_25",
        "OLD_PED_65_AND_OVER",
        "OLD_DRIVER_75_AND_OVER",
        "HEAVYVEHICLE",
        "MOTORCYCLE",
        "DEG_URBAN_NAME",
        "RMA",
        "DIVIDED",
    ]
    select_list = ", ".join(columns)
    duckdb.sql(
        f"""
        COPY (
          SELECT {select_list}
          FROM read_csv('{source.as_posix()}', header = true, all_varchar = true, ignore_errors = true)
          WHERE SEVERITY IN ('Fatal accident', 'Serious injury accident')
            AND ACCIDENT_DATE >= '2018-01-01'
        ) TO '{dest.as_posix()}' (FORMAT CSV, HEADER true, COMPRESSION gzip);
        """
    )
    return _rows_gz(dest)


def _round_coords(node):
    if isinstance(node, list):
        if node and isinstance(node[0], (int, float)) and not isinstance(node[0], bool):
            return [round(float(value), 3) for value in node]
        return [_round_coords(child) for child in node]
    return node


def _write_map(source: Path, dest: Path) -> int:
    raw = json.loads(source.read_text())
    features = []
    for feature in raw["features"]:
        props = feature["properties"]
        name = props.get("name")
        if props.get("admin") != "Australia" or name not in STATE_CODE:
            continue
        features.append(
            {
                "type": "Feature",
                "properties": {
                    "code": STATE_CODE[name],
                    "name": name,
                    "label_lon": props.get("longitude"),
                    "label_lat": props.get("latitude"),
                },
                "geometry": _round_coords(feature["geometry"]),
            }
        )
    features.sort(key=lambda item: item["properties"]["code"])
    dest.write_text(json.dumps({"type": "FeatureCollection", "features": features}))
    return len(features)


def _write_rules(dest: Path) -> int:
    rows = [
        ("deaths_reduction", "50", "percent", "Halve the number of road deaths by 2030"),
        ("deaths_baseline_start", "2018", "year", "Baseline is the average of these calendar years"),
        ("deaths_baseline_end", "2020", "year", "Baseline is the average of these calendar years"),
        ("horizon_year", "2030", "year", "End of the National Road Safety Strategy"),
        ("per_capita_deaths_reduction", "55", "percent", "Per-person death-rate reduction aimed for by 2030"),
        ("serious_injury_reduction", "30", "percent", "Serious-injury reduction aimed for by 2030"),
        ("trajectory", "linear", "shape", "Straight line from the baseline at end-2020 to the 2030 target"),
    ]
    with dest.open("w", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(["parameter", "value", "unit", "meaning", "source_url"])
        for parameter, value, unit, meaning in rows:
            writer.writerow([parameter, value, unit, meaning, NRSS_URL])
    return len(rows)


def _entry(path: Path, **meta) -> dict:
    rows = meta.pop("rows")
    return {
        "path": str(path.relative_to(ROOT)),
        "sha256": _sha256(path),
        "bytes": path.stat().st_size,
        "rows": rows,
        "access_date": ACCESS_DATE,
        **meta,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--cache-dir", type=Path, default=CACHE)
    args = parser.parse_args()
    cache = args.cache_dir
    cache.mkdir(parents=True, exist_ok=True)
    SNAPSHOT.mkdir(parents=True, exist_ok=True)

    ardd = cache / "bitre_fatalities_sep2026.xlsx"
    erp = cache / "erp_q.csv"
    sa4 = cache / "sa4.csv"
    vic = cache / "victorian_road_crash_data.csv"
    maps = cache / "ne50.geojson"
    _download(ARDD_URL, ardd)
    _download(ERP_URL, erp)
    _download(SA4_URL, sa4, headers={"Accept": "application/vnd.sdmx.data+csv;labels=name", "User-Agent": "road-toll-reversal/1.0"})
    _download(VIC_URL, vic)
    _download(MAP_URL, maps)

    fatalities = SNAPSHOT / "ardd_fatalities.csv.gz"
    erp_out = SNAPSHOT / "abs_erp_state_age.csv.gz"
    sa4_out = SNAPSHOT / "abs_sa4_population.csv.gz"
    vic_out = SNAPSHOT / "vic_serious_fatal_crashes.csv.gz"
    map_out = SNAPSHOT / "aus_states.geojson"
    rules_out = SNAPSHOT / "nrss_target_rules.csv"

    n_fatal = _write_fatalities(ardd, fatalities)
    n_erp = _write_erp(erp, erp_out)
    n_sa4 = _write_sa4(sa4, sa4_out)
    n_vic = _write_vic(vic, vic_out)
    n_map = _write_map(maps, map_out)
    n_rules = _write_rules(rules_out)

    manifest = {
        "project": "road-toll-reversal",
        "access_date": ACCESS_DATE,
        "note": (
            "Snapshot files are reproducible extracts of free, openly licensed public data. "
            "sha256 values are of the files in this directory. Raw downloads are not all committed; "
            "their URLs and licences are recorded here. No paid API and no fabricated rows."
        ),
        "files": [
            _entry(
                fatalities,
                rows=n_fatal,
                title="Australian Road Deaths Database fatalities",
                publisher="Bureau of Infrastructure and Transport Research Economics (BITRE)",
                url=ARDD_URL,
                landing_page="https://catalogue.data.infrastructure.gov.au/dataset/australian-road-deaths-database",
                licence="Creative Commons Attribution 3.0 Australia",
                licence_url="https://creativecommons.org/licenses/by/3.0/au/",
                coverage="Person-level road deaths, 1989 to September 2026",
                notes=(
                    "Extracted from bitre_fatalities_sep2026.xlsx sheet BITRE_Fatality "
                    "(title rows removed). From May 2025 BITRE no longer publishes a separate "
                    "fatal-crashes file; crash attributes are on each fatality row and fatal crashes "
                    "are the distinct Crash ID values. Recent months are preliminary."
                ),
                source_sha256=_sha256(ardd),
            ),
            _entry(
                erp_out,
                rows=n_erp,
                title="Quarterly estimated resident population by state, sex and age",
                publisher="Australian Bureau of Statistics",
                url=ERP_URL,
                landing_page="https://www.abs.gov.au/statistics/people/population/national-state-and-territory-population",
                licence="Creative Commons Attribution 4.0 International",
                licence_url="https://creativecommons.org/licenses/by/4.0/",
                coverage="2017 Q4 to 2026 Q1, states and Australia, persons by single year of age plus male and female totals",
                notes=(
                    "ABS Data API dataflow ERP_Q, measure 1 (estimated resident population), no API key. "
                    "Five-year age aggregates were dropped so single years are not double-counted. "
                    "The newest quarter in this extract is 2026 Q1 (31 March 2026)."
                ),
            ),
            _entry(
                sa4_out,
                rows=n_sa4,
                title="Annual estimated resident population by SA4",
                publisher="Australian Bureau of Statistics",
                url=SA4_URL,
                landing_page="https://www.abs.gov.au/statistics/people/population/regional-population",
                licence="Creative Commons Attribution 4.0 International",
                licence_url="https://creativecommons.org/licenses/by/4.0/",
                coverage="Statistical Area Level 4, 30 June 2018 to 30 June 2025, ASGS Edition 3",
                notes="ABS Data API dataflow ABS_ANNUAL_ERP_ASGS2021, region type SA4, measure ERP. Free, no API key.",
            ),
            _entry(
                vic_out,
                rows=n_vic,
                title="Victoria fatal and serious-injury crashes",
                publisher="Department of Transport and Planning, Victoria",
                url=VIC_URL,
                landing_page="https://discover.data.vic.gov.au/dataset/victoria-road-crash-data",
                licence="Creative Commons Attribution 4.0 International",
                licence_url="https://creativecommons.org/licenses/by/4.0/",
                coverage="Fatal and serious-injury crashes from 1 January 2018. Source file runs to 28 March 2026; 2026 is partial.",
                notes=(
                    "Subset of the Victorian Road Crash Data flat file: fatal and serious-injury crashes only, "
                    "analysis columns only. Other-injury crashes were left out to keep the snapshot small. "
                    "This is a state collection and is not the same count as BITRE deaths."
                ),
            ),
            _entry(
                map_out,
                rows=n_map,
                title="Australia state and territory outlines",
                publisher="Natural Earth",
                url=MAP_URL,
                landing_page="https://www.naturalearthdata.com/",
                licence="Public domain",
                licence_url="https://www.naturalearthdata.com/about/terms-of-use/",
                coverage="1:50m admin-1 polygons for the eight states and territories",
                notes=(
                    "The ABS ArcGIS boundary service returned an error on the access date, so the dashboard "
                    "ships these public-domain outlines. Coordinates are rounded to three decimal places. "
                    "Jervis Bay Territory is omitted. Statistical numbers do not come from this file."
                ),
            ),
            _entry(
                rules_out,
                rows=n_rules,
                title="National Road Safety Strategy target rules",
                publisher="Office of Road Safety / Department of Infrastructure",
                url=NRSS_URL,
                landing_page="https://www.roadsafety.gov.au/nrss",
                licence="Published Australian Government policy parameters, reproduced with attribution",
                licence_url="https://www.roadsafety.gov.au/nrss/fact-sheets/performance-monitoring",
                coverage="Headline 2021–30 targets used to draw the path. Death counts are computed from BITRE, not copied.",
                notes=(
                    "Parameters only: 50 percent fewer deaths and a 55 percent lower per-person rate by 2030, "
                    "against the 2018–2020 average, on a straight path. The Strategy does not set state targets; "
                    "state paths apply that same national rule to each state's own baseline, which is how the "
                    "AAA NRSS benchmark reads jurisdictional progress."
                ),
            ),
        ],
    }
    (ROOT / "data" / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
    print(json.dumps({item["path"]: item["rows"] for item in manifest["files"]}, indent=2))


if __name__ == "__main__":
    sys.exit(main())
