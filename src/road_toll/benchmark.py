"""Same gold queries on DuckDB and ClickHouse.

ClickHouse is the optional serving engine. DuckDB remains the default. Both are
free. The comparison is latency on this project's tables, not a cloud bill.
"""

from __future__ import annotations

import base64
import json
import os
import time
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor

import duckdb

from road_toll.paths import BENCHMARK_DIR, DB_PATH

CLICKHOUSE_URL = os.environ.get("CLICKHOUSE_URL", "http://localhost:8123").rstrip("/")
USER = os.environ.get("CLICKHOUSE_USER", "default")
PASSWORD = os.environ.get("CLICKHOUSE_PASSWORD", "roadtoll")
REPEATS = int(os.environ.get("BENCH_REPEATS", "25"))

QUERIES = {
    "latest_by_state": """
        SELECT state, count(*) AS deaths
        FROM fatalities
        WHERE (year = 2025 AND month >= 10) OR (year = 2026 AND month <= 9)
        GROUP BY state
        ORDER BY deaths DESC
    """,
    "vulnerable_by_month": """
        SELECT year, month, road_user_group, count(*) AS deaths
        FROM fatalities
        WHERE road_user_group IN ('Pedestrian', 'Motorcyclist', 'Cyclist')
          AND year >= 2018
        GROUP BY year, month, road_user_group
    """,
    "afternoon_speed": """
        SELECT state, speed_band, count(*) AS deaths
        FROM fatalities
        WHERE hour BETWEEN 14 AND 18 AND year >= 2022
        GROUP BY state, speed_band
        ORDER BY deaths DESC
    """,
    "sa4_group_totals": """
        SELECT sa4_name, road_user_group, sum(deaths) AS deaths
        FROM sa4_group_year
        GROUP BY sa4_name, road_user_group
        ORDER BY deaths DESC
        LIMIT 50
    """,
}


def _ch(query: str, body: bytes | None = None) -> bytes:
    target = CLICKHOUSE_URL if body is None else f"{CLICKHOUSE_URL}/?query={urllib.parse.quote(query)}"
    request = urllib.request.Request(target, data=body if body is not None else query.encode(), method="POST")
    token = base64.b64encode(f"{USER}:{PASSWORD}".encode()).decode()
    request.add_header("Authorization", "Basic " + token)
    with urllib.request.urlopen(request, timeout=120) as response:
        return response.read()


def _prepare_clickhouse() -> None:
    _ch("CREATE DATABASE IF NOT EXISTS toll")
    _ch(
        """
        CREATE TABLE IF NOT EXISTS toll.fatalities (
          state String, year Int32, month Int32, hour Int32,
          road_user_group String, speed_band String
        ) ENGINE = MergeTree ORDER BY (year, month, state)
        """
    )
    _ch("TRUNCATE TABLE toll.fatalities")
    _ch(
        """
        CREATE TABLE IF NOT EXISTS toll.sa4_group_year (
          sa4_name String, road_user_group String, deaths Int32
        ) ENGINE = MergeTree ORDER BY (sa4_name, road_user_group)
        """
    )
    _ch("TRUNCATE TABLE toll.sa4_group_year")
    con = duckdb.connect(str(DB_PATH), read_only=True)
    deaths = con.execute(
        """
        SELECT state, year, month, coalesce(hour, -1) AS hour, road_user_group, speed_band
        FROM fatalities
        """
    ).fetchdf()
    groups = con.execute(
        "SELECT sa4_name, road_user_group, deaths FROM sa4_group_year"
    ).fetchdf()
    con.close()
    _ch("INSERT INTO toll.fatalities FORMAT CSV", deaths.to_csv(index=False, header=False).encode())
    _ch(
        "INSERT INTO toll.sa4_group_year FORMAT CSV",
        groups.to_csv(index=False, header=False).encode(),
    )


def _translate(query: str) -> str:
    return query.replace("FROM fatalities", "FROM toll.fatalities").replace(
        "FROM sa4_group_year", "FROM toll.sa4_group_year"
    )


def _time_duckdb() -> dict[str, float]:
    con = duckdb.connect(str(DB_PATH), read_only=True)
    timings = {}
    for name, query in QUERIES.items():
        con.execute(query).fetchall()
        started = time.perf_counter()
        for _ in range(REPEATS):
            con.execute(query).fetchall()
        timings[name] = (time.perf_counter() - started) / REPEATS * 1000
    con.close()
    return timings


def _time_clickhouse() -> dict[str, float]:
    timings = {}
    for name, query in QUERIES.items():
        sql = _translate(query)
        _ch(sql)
        started = time.perf_counter()
        for _ in range(REPEATS):
            _ch(sql)
        timings[name] = (time.perf_counter() - started) / REPEATS * 1000
    return timings


def _parallel(kind: str) -> float:
    sql = QUERIES["latest_by_state"]

    def run_duck():
        con = duckdb.connect(str(DB_PATH), read_only=True)
        con.execute(sql).fetchall()
        con.close()

    def run_house():
        _ch(_translate(sql))

    started = time.perf_counter()
    worker = run_duck if kind == "duckdb" else run_house
    with ThreadPoolExecutor(max_workers=8) as pool:
        list(pool.map(lambda _: worker(), range(8)))
    return (time.perf_counter() - started) * 1000


def run_benchmark() -> dict:
    _prepare_clickhouse()
    result = {
        "repeats": REPEATS,
        "unit": "milliseconds, mean of repeated runs after one warmup",
        "duckdb_ms": _time_duckdb(),
        "clickhouse_ms": _time_clickhouse(),
        "eight_at_once_latest_by_state_ms": {
            "duckdb": _parallel("duckdb"),
            "clickhouse": _parallel("clickhouse"),
        },
        "scale": "59,169 death rows and the community-year table built from them",
        "cost": "Open-source engines on one machine. No cloud warehouse and no spend.",
    }
    BENCHMARK_DIR.mkdir(parents=True, exist_ok=True)
    (BENCHMARK_DIR / "results.json").write_text(json.dumps(result, indent=2) + "\n")
    print(json.dumps(result, indent=2))
    return result
