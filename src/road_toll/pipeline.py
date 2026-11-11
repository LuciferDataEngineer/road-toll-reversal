"""Bronze, silver, and gold tables on the committed snapshot.

DuckDB is the default warehouse. Cleaning is SQL. The 2030 path uses the same
factor function the tests lock, then the results are stored back in DuckDB.
"""

from __future__ import annotations

import json
from collections import defaultdict

import duckdb

from road_toll.metrics import days_in_month, mean_year_end_factor, round_people, trajectory_factor
from road_toll.paths import (
    ABS_REGION,
    AGE_BANDS,
    BASELINE_YEARS,
    DB_PATH,
    FACTS_PATH,
    GOLD_DIR,
    LATEST_MONTH,
    LATEST_YEAR,
    MODEL_YEARS,
    ROAD_USERS,
    SNAPSHOT,
    STATE_NAMES,
    STATES,
    STE_FROM_DIGIT,
    VULNERABLE,
)

WINDOW_START = (2025, 10)  # October 2025 through September 2026


def connect(read_only: bool = False) -> duckdb.DuckDBPyConnection:
    GOLD_DIR.mkdir(parents=True, exist_ok=True)
    return duckdb.connect(str(DB_PATH), read_only=read_only)


def reset_warehouse() -> None:
    for suffix in ("", ".wal"):
        path = GOLD_DIR / f"road_toll.duckdb{suffix}"
        if path.exists():
            path.unlink()


def _snapshot(name: str) -> str:
    path = SNAPSHOT / name
    if not path.exists():
        raise FileNotFoundError(f"Missing snapshot file {path}. Run scripts/refresh_snapshot.py.")
    return path.as_posix()


def build_crashes() -> None:
    """Crashes domain: person-level deaths and Victoria fatal/serious crashes."""
    con = connect()
    fatalities = _snapshot("ardd_fatalities.csv.gz")
    victoria = _snapshot("vic_serious_fatal_crashes.csv.gz")
    con.execute(
        f"""
        CREATE OR REPLACE TABLE fatalities AS
        WITH raw AS (
          SELECT * FROM read_csv('{fatalities}', header = true, union_by_name = true)
        ),
        typed AS (
          SELECT
            CAST(crash_id AS VARCHAR) AS crash_id,
            CAST(state AS VARCHAR) AS state,
            CAST(year AS INTEGER) AS year,
            CAST(month AS INTEGER) AS month,
            CAST(day_of_week AS VARCHAR) AS day_of_week,
            try_cast(split_part(CAST(time AS VARCHAR), ':', 1) AS INTEGER) AS hour_raw,
            CAST(crash_type AS VARCHAR) AS crash_type,
            CAST(road_user AS VARCHAR) AS road_user,
            CAST(gender AS VARCHAR) AS gender_raw,
            try_cast(age AS INTEGER) AS age_raw,
            try_cast(speed_limit AS INTEGER) AS speed_raw,
            CAST(remoteness AS VARCHAR) AS remoteness_raw,
            CAST(sa4_name AS VARCHAR) AS sa4_name,
            CAST(road_type AS VARCHAR) AS road_type_raw,
            CAST(christmas_period AS VARCHAR) AS christmas_raw,
            CAST(easter_period AS VARCHAR) AS easter_raw,
            CAST(bus_involvement AS VARCHAR) AS bus_raw,
            CAST(heavy_rigid_truck AS VARCHAR) AS rigid_raw,
            CAST(articulated_truck AS VARCHAR) AS articulated_raw
          FROM raw
          WHERE state IN {STATES}
            AND year BETWEEN 1989 AND {LATEST_YEAR}
            AND month BETWEEN 1 AND 12
        )
        SELECT
          row_number() OVER (ORDER BY year, month, state, crash_id) AS fatality_id,
          crash_id,
          state,
          year,
          month,
          make_date(year, month, 1) AS month_start,
          day_of_week,
          CASE WHEN hour_raw BETWEEN 0 AND 23 THEN hour_raw END AS hour,
          CASE
            WHEN road_user IN ('Motorcycle rider', 'Motorcycle pillion passenger') THEN 'Motorcyclist'
            WHEN road_user = 'Pedal cyclist' THEN 'Cyclist'
            WHEN road_user IN ('Pedestrian', 'Driver', 'Passenger') THEN road_user
            ELSE 'Unknown'
          END AS road_user_group,
          CASE WHEN gender_raw IN ('Male', 'Female') THEN gender_raw ELSE 'Unknown' END AS gender,
          CASE WHEN age_raw BETWEEN 0 AND 120 THEN age_raw END AS age,
          CASE
            WHEN age_raw BETWEEN 0 AND 7 THEN '0–7'
            WHEN age_raw BETWEEN 8 AND 16 THEN '8–16'
            WHEN age_raw BETWEEN 17 AND 25 THEN '17–25'
            WHEN age_raw BETWEEN 26 AND 39 THEN '26–39'
            WHEN age_raw BETWEEN 40 AND 64 THEN '40–64'
            WHEN age_raw BETWEEN 65 AND 74 THEN '65–74'
            WHEN age_raw BETWEEN 75 AND 120 THEN '75+'
            ELSE 'Unknown'
          END AS age_band,
          CASE
            WHEN speed_raw IS NULL OR speed_raw < 0 THEN 'Unknown'
            WHEN speed_raw <= 50 THEN 'Up to 50 km/h'
            WHEN speed_raw <= 80 THEN '60–80 km/h'
            ELSE '90 km/h and above'
          END AS speed_band,
          CASE remoteness_raw
            WHEN 'Major Cities of Australia' THEN 'Major cities'
            WHEN 'Inner Regional Australia' THEN 'Inner regional'
            WHEN 'Outer Regional Australia' THEN 'Outer regional'
            WHEN 'Remote Australia' THEN 'Remote'
            WHEN 'Very Remote Australia' THEN 'Very remote'
            ELSE 'Unknown'
          END AS remoteness,
          CASE road_type_raw
            WHEN 'National or State Highway' THEN 'Highway'
            WHEN 'Arterial Road' THEN 'Arterial'
            WHEN 'Sub-arterial Road' THEN 'Sub-arterial'
            WHEN 'Collector Road' THEN 'Collector'
            WHEN 'Local Road' THEN 'Local street'
            WHEN 'Access road' THEN 'Access road'
            ELSE 'Unknown'
          END AS road_type,
          coalesce(christmas_raw = 'Yes', false) AS christmas_period,
          coalesce(easter_raw = 'Yes', false) AS easter_period,
          month IN (12, 1) AS holiday_season,
          CASE
            WHEN hour_raw BETWEEN 0 AND 5 OR hour_raw BETWEEN 18 AND 23 THEN 'Night'
            WHEN hour_raw BETWEEN 6 AND 17 THEN 'Daytime'
            ELSE 'Unknown'
          END AS day_part,
          crash_type,
          lower(trim(sa4_name)) AS sa4_name_key,
          CASE WHEN sa4_name IS NULL OR sa4_name IN ('Unknown', '') THEN NULL ELSE sa4_name END AS sa4_name,
          coalesce(rigid_raw = 'Yes' OR articulated_raw = 'Yes', false) AS heavy_truck,
          coalesce(bus_raw = 'Yes', false) AS bus
        FROM typed
        """
    )
    con.execute(
        """
        CREATE OR REPLACE VIEW fatalities_all AS
        SELECT * FROM fatalities
        UNION ALL
        SELECT * REPLACE ('AUS' AS state) FROM fatalities
        """
    )
    con.execute(
        f"""
        CREATE OR REPLACE TABLE vic_crashes AS
        WITH raw AS (
          SELECT * FROM read_csv('{victoria}', header = true, union_by_name = true, all_varchar = true)
        )
        SELECT
          ACCIDENT_NO AS accident_no,
          CAST(ACCIDENT_DATE AS DATE) AS accident_date,
          year(CAST(ACCIDENT_DATE AS DATE)) AS year,
          month(CAST(ACCIDENT_DATE AS DATE)) AS month,
          ACCIDENT_TYPE AS accident_type,
          DAY_OF_WEEK AS day_of_week,
          LIGHT_CONDITION AS light_condition,
          ROAD_GEOMETRY AS road_geometry,
          SEVERITY AS severity,
          try_cast(regexp_extract(SPEED_ZONE, '([0-9]+)', 1) AS INTEGER) AS speed_limit,
          DTP_REGION AS dtp_region,
          LGA_NAME AS lga_name,
          RMA AS road_manager,
          try_cast(FATALITY AS INTEGER) AS deaths,
          try_cast(SERIOUSINJURY AS INTEGER) AS serious_injuries,
          try_cast(PEDESTRIAN AS INTEGER) AS pedestrians_involved,
          try_cast(BICYCLIST AS INTEGER) AS cyclists_involved,
          try_cast(MOTORCYCLIST AS INTEGER) + coalesce(try_cast(PILLION AS INTEGER), 0) AS motorcyclists_involved,
          coalesce(try_cast(HEAVYVEHICLE AS INTEGER), 0) > 0 AS heavy_vehicle
        FROM raw
        WHERE ACCIDENT_DATE >= '2018-01-01'
        """
    )
    con.close()


def build_population() -> None:
    """Population domain: state age bands and SA4 totals."""
    con = connect()
    erp = _snapshot("abs_erp_state_age.csv.gz")
    sa4 = _snapshot("abs_sa4_population.csv.gz")
    region_case = " ".join(
        f"WHEN '{code}' THEN '{state}'" for state, code in ABS_REGION.items()
    )
    con.execute(
        f"""
        CREATE OR REPLACE TABLE erp_state_age AS
        WITH raw AS (
          SELECT
            CASE region_code {region_case} END AS state,
            sex_code,
            age,
            CAST(split_part(time_period, '-Q', 1) AS INTEGER) AS year,
            CAST(split_part(time_period, '-Q', 2) AS INTEGER) AS quarter,
            CAST(round(population) AS BIGINT) AS population
          FROM read_csv('{erp}', header = true)
        ),
        all_ages AS (
          SELECT state, year, quarter, 'All ages' AS age_band, population
          FROM raw
          WHERE sex_code = '3' AND age = 'TOT' AND state IS NOT NULL
        ),
        bands AS (
          SELECT
            state,
            year,
            quarter,
            CASE
              WHEN try_cast(age AS INTEGER) BETWEEN 0 AND 7 THEN '0–7'
              WHEN try_cast(age AS INTEGER) BETWEEN 8 AND 16 THEN '8–16'
              WHEN try_cast(age AS INTEGER) BETWEEN 17 AND 25 THEN '17–25'
              WHEN try_cast(age AS INTEGER) BETWEEN 26 AND 39 THEN '26–39'
              WHEN try_cast(age AS INTEGER) BETWEEN 40 AND 64 THEN '40–64'
              WHEN try_cast(age AS INTEGER) BETWEEN 65 AND 74 THEN '65–74'
              WHEN try_cast(age AS INTEGER) BETWEEN 75 AND 120 THEN '75+'
            END AS age_band,
            population
          FROM raw
          WHERE sex_code = '3' AND age != 'TOT' AND state IS NOT NULL
        )
        SELECT state, year, quarter, age_band, population FROM all_ages
        UNION ALL
        SELECT state, year, quarter, age_band, sum(population)::BIGINT AS population
        FROM bands
        WHERE age_band IS NOT NULL
        GROUP BY 1, 2, 3, 4
        """
    )
    con.execute(
        f"""
        CREATE OR REPLACE TABLE erp_sex AS
        SELECT
          CASE region_code {region_case} END AS state,
          CASE sex_code WHEN '1' THEN 'Male' WHEN '2' THEN 'Female' WHEN '3' THEN 'People' END AS sex,
          CAST(split_part(time_period, '-Q', 1) AS INTEGER) AS year,
          CAST(split_part(time_period, '-Q', 2) AS INTEGER) AS quarter,
          CAST(round(population) AS BIGINT) AS population
        FROM read_csv('{erp}', header = true)
        WHERE age = 'TOT'
        """
    )
    con.execute(
        f"""
        CREATE OR REPLACE TABLE sa4_population AS
        SELECT
          CAST(sa4_code AS VARCHAR) AS sa4_code,
          sa4_name,
          lower(trim(sa4_name)) AS sa4_name_key,
          CASE substr(CAST(sa4_code AS VARCHAR), 1, 1)
            {' '.join(f"WHEN '{digit}' THEN '{state}'" for digit, state in STE_FROM_DIGIT.items())}
          END AS state,
          CAST(year AS INTEGER) AS year,
          CAST(population AS BIGINT) AS population
        FROM read_csv('{sa4}', header = true)
        WHERE population > 0
        """
    )
    con.close()


def _in_latest_window(year: int, month: int) -> bool:
    return (year, month) >= WINDOW_START and (year, month) <= (LATEST_YEAR, LATEST_MONTH)


def build_targets_and_serving() -> dict:
    """Targets domain plus the decision tables that join deaths, people, and the path."""
    con = connect()
    monthly_rows = con.execute(
        """
        SELECT state, year, month, count(*)::INTEGER AS deaths
        FROM fatalities_all
        GROUP BY 1, 2, 3
        ORDER BY 1, 2, 3
        """
    ).fetchall()
    by_state: dict[str, dict[tuple[int, int], int]] = defaultdict(dict)
    for state, year, month, deaths in monthly_rows:
        by_state[state][(int(year), int(month))] = int(deaths)

    baseline_rows = con.execute(
        f"""
        SELECT state, sum(deaths) / {len(BASELINE_YEARS)} AS baseline_annual
        FROM (
          SELECT state, year, count(*)::DOUBLE AS deaths
          FROM fatalities_all
          WHERE year BETWEEN {BASELINE_YEARS[0]} AND {BASELINE_YEARS[-1]}
          GROUP BY 1, 2
        )
        GROUP BY 1
        """
    ).fetchall()
    baseline = {state: float(value) for state, value in baseline_rows}

    path_rows = []
    months = []
    year, month = 2010, 1
    while (year, month) <= (2030, 12):
        months.append((year, month))
        if month == 12:
            year, month = year + 1, 1
        else:
            month += 1

    for state in ("AUS", *STATES):
        series = [by_state[state].get(key, 0) for key in months]
        rolling = _rolling(series)
        base = baseline[state]
        for (year, month), roll in zip(months, rolling, strict=True):
            if (year, month) < (2018, 1):
                continue
            factor = trajectory_factor(year, month) if (year, month) >= (2020, 12) else None
            target = None if factor is None or roll is None else base * factor
            actual = roll if (year, month) <= (LATEST_YEAR, LATEST_MONTH) else None
            excess = None if actual is None or target is None else actual - target
            path_rows.append(
                (state, year, month, base, factor, target, actual, excess)
            )

    con.execute("CREATE OR REPLACE TABLE state_month_path (state VARCHAR, year INTEGER, month INTEGER, baseline_annual DOUBLE, trajectory_factor DOUBLE, target_deaths DOUBLE, rolling_deaths DOUBLE, excess_deaths DOUBLE)")
    con.executemany("INSERT INTO state_month_path VALUES (?, ?, ?, ?, ?, ?, ?, ?)", path_rows)

    factor_now = trajectory_factor(LATEST_YEAR, LATEST_MONTH)
    con.execute(
        f"""
        CREATE OR REPLACE TABLE slice_latest AS
        WITH recent AS (
          SELECT state, road_user_group AS slice_value, count(*)::DOUBLE AS recent_deaths
          FROM fatalities_all
          WHERE (year = 2025 AND month >= 10) OR (year = 2026 AND month <= 9)
          GROUP BY 1, 2
        ),
        base AS (
          SELECT state, road_user_group AS slice_value, count(*)::DOUBLE / {len(BASELINE_YEARS)} AS baseline_annual
          FROM fatalities_all
          WHERE year BETWEEN {BASELINE_YEARS[0]} AND {BASELINE_YEARS[-1]}
          GROUP BY 1, 2
        )
        SELECT
          'road_user' AS slice_type,
          coalesce(recent.state, base.state) AS state,
          coalesce(recent.slice_value, base.slice_value) AS slice_value,
          coalesce(base.baseline_annual, 0) AS baseline_annual,
          coalesce(recent.recent_deaths, 0) AS recent_deaths,
          coalesce(base.baseline_annual, 0) * {factor_now} AS target_deaths,
          coalesce(recent.recent_deaths, 0) - coalesce(base.baseline_annual, 0) * {factor_now} AS excess_deaths
        FROM recent
        FULL OUTER JOIN base
          ON recent.state = base.state AND recent.slice_value = base.slice_value
        """
    )
    _slice_dimension(con, "age_band", "age_band", factor_now)
    _slice_dimension(con, "gender", "gender", factor_now)
    _slice_dimension(con, "day_of_week", "day_of_week", factor_now)
    _slice_dimension(con, "day_part", "day_part", factor_now)
    _slice_dimension(con, "speed_band", "speed_band", factor_now)
    _slice_dimension(con, "remoteness", "remoteness", factor_now)
    _slice_dimension(con, "road_type", "road_type", factor_now)
    _slice_dimension(
        con,
        "holiday_season",
        "CASE WHEN holiday_season THEN 'December and January' ELSE 'Rest of the year' END",
        factor_now,
        expression=True,
    )
    con.execute(
        """
        INSERT INTO slice_latest
        SELECT 'heavy_truck', state,
               CASE WHEN heavy_truck THEN 'Heavy truck involved' ELSE 'No heavy truck' END,
               0, count(*)::DOUBLE, NULL, NULL
        FROM fatalities_all
        WHERE (year = 2025 AND month >= 10) OR (year = 2026 AND month <= 9)
        GROUP BY state, 3
        """
    )

    # Population at the latest published quarter, and June snapshots for baseline rates.
    pop_now = _population_map(con, 2026, 1, "All ages")
    pop_age_now = {
        band: _population_map(con, 2026, 1, band) for band in AGE_BANDS
    }
    pop_sex_now = _sex_population(con, 2026, 1)
    group_target = _group_target_rates(con, pop_now)
    con.execute(
        """
        CREATE OR REPLACE TABLE group_target_rate (
          state VARCHAR,
          road_user_group VARCHAR,
          baseline_rate DOUBLE,
          target_rate DOUBLE
        )
        """
    )
    con.executemany(
        "INSERT INTO group_target_rate VALUES (?, ?, ?, ?)",
        [
            (state, group, rates["baseline_rate"], rates["target_rate"])
            for (state, group), rates in group_target.items()
        ],
    )

    con.execute(
        f"""
        CREATE OR REPLACE TABLE sa4_group_year AS
        WITH pop AS (
          SELECT sa4_code, sa4_name, sa4_name_key, state, year, population
          FROM sa4_population
          WHERE year BETWEEN {MODEL_YEARS[0]} AND {MODEL_YEARS[-1]}
            AND state IS NOT NULL
        ),
        groups AS (
          SELECT unnest({list(ROAD_USERS)}) AS road_user_group
        ),
        grid AS (
          SELECT pop.*, groups.road_user_group
          FROM pop CROSS JOIN groups
        ),
        deaths AS (
          SELECT sa4_name_key, year, road_user_group, count(*)::INTEGER AS deaths
          FROM fatalities
          WHERE year BETWEEN {MODEL_YEARS[0]} AND {MODEL_YEARS[-1]}
            AND road_user_group IN {ROAD_USERS}
            AND sa4_name IS NOT NULL
          GROUP BY 1, 2, 3
        )
        SELECT
          grid.sa4_code,
          grid.sa4_name,
          grid.state,
          grid.road_user_group,
          grid.year,
          coalesce(deaths.deaths, 0)::INTEGER AS deaths,
          grid.population
        FROM grid
        LEFT JOIN deaths
          ON grid.sa4_name_key = deaths.sa4_name_key
         AND grid.year = deaths.year
         AND grid.road_user_group = deaths.road_user_group
        """
    )
    matched = con.execute(
        """
        SELECT
          count(DISTINCT sa4_name) FILTER (WHERE year >= 2022) AS named,
          count(DISTINCT sa4_name) FILTER (
            WHERE year >= 2022 AND sa4_name_key IN (SELECT sa4_name_key FROM sa4_population)
          ) AS matched
        FROM fatalities
        WHERE sa4_name IS NOT NULL
        """
    ).fetchone()

    facts = _assemble_facts(
        con,
        baseline=baseline,
        by_state=by_state,
        factor_now=factor_now,
        pop_now=pop_now,
        pop_age_now=pop_age_now,
        pop_sex_now=pop_sex_now,
        group_target=group_target,
        sa4_match=(int(matched[1]), int(matched[0])),
    )
    FACTS_PATH.write_text(json.dumps(facts, indent=2) + "\n")
    con.close()
    return facts


def _rolling(series: list[int]) -> list[float | None]:
    from road_toll.metrics import rolling_sum

    return rolling_sum(series, 12)


def _slice_dimension(con, slice_type: str, column: str, factor: float, expression: bool = False) -> None:
    value_expr = column if expression else column
    con.execute(
        f"""
        INSERT INTO slice_latest
        WITH recent AS (
          SELECT state, {value_expr} AS slice_value, count(*)::DOUBLE AS recent_deaths
          FROM fatalities_all
          WHERE (year = 2025 AND month >= 10) OR (year = 2026 AND month <= 9)
          GROUP BY 1, 2
        ),
        base AS (
          SELECT state, {value_expr} AS slice_value, count(*)::DOUBLE / {len(BASELINE_YEARS)} AS baseline_annual
          FROM fatalities_all
          WHERE year BETWEEN {BASELINE_YEARS[0]} AND {BASELINE_YEARS[-1]}
          GROUP BY 1, 2
        )
        SELECT
          '{slice_type}',
          coalesce(recent.state, base.state),
          coalesce(recent.slice_value, base.slice_value),
          coalesce(base.baseline_annual, 0),
          coalesce(recent.recent_deaths, 0),
          coalesce(base.baseline_annual, 0) * {factor},
          coalesce(recent.recent_deaths, 0) - coalesce(base.baseline_annual, 0) * {factor}
        FROM recent
        FULL OUTER JOIN base
          ON recent.state = base.state AND recent.slice_value = base.slice_value
        """
    )


def _population_map(con, year: int, quarter: int, age_band: str) -> dict[str, int]:
    rows = con.execute(
        """
        SELECT state, population
        FROM erp_state_age
        WHERE year = ? AND quarter = ? AND age_band = ?
        """,
        [year, quarter, age_band],
    ).fetchall()
    return {state: int(pop) for state, pop in rows}


def _sex_population(con, year: int, quarter: int) -> dict[tuple[str, str], int]:
    rows = con.execute(
        """
        SELECT state, sex, population FROM erp_sex
        WHERE year = ? AND quarter = ?
        """,
        [year, quarter],
    ).fetchall()
    return {(state, sex): int(pop) for state, sex, pop in rows}


def _group_target_rates(con, pop_now: dict[str, int]) -> dict[tuple[str, str], dict]:
    """Deaths per 100,000 at the 2018–20 baseline, and on the 2022–25 average path."""
    factor = mean_year_end_factor(MODEL_YEARS)
    rows = con.execute(
        f"""
        WITH june AS (
          SELECT state, year, population
          FROM erp_state_age
          WHERE quarter = 2 AND age_band = 'All ages'
            AND year BETWEEN {BASELINE_YEARS[0]} AND {BASELINE_YEARS[-1]}
        ),
        pop AS (
          SELECT state, avg(population) AS population
          FROM june
          GROUP BY 1
        ),
        deaths AS (
          SELECT state, road_user_group, count(*)::DOUBLE / {len(BASELINE_YEARS)} AS baseline_annual
          FROM fatalities_all
          WHERE year BETWEEN {BASELINE_YEARS[0]} AND {BASELINE_YEARS[-1]}
            AND road_user_group IN {ROAD_USERS}
          GROUP BY 1, 2
        )
        SELECT deaths.state, deaths.road_user_group, deaths.baseline_annual, pop.population
        FROM deaths
        JOIN pop ON deaths.state = pop.state
        """
    ).fetchall()
    rates = {}
    for state, group, baseline_annual, population in rows:
        baseline_rate = float(baseline_annual) / float(population) * 100_000
        rates[(state, group)] = {
            "baseline_rate": baseline_rate,
            "target_rate": baseline_rate * factor,
            "population_now": pop_now.get(state, 0),
        }
    return rates


def _assemble_facts(
    con,
    *,
    baseline,
    by_state,
    factor_now,
    pop_now,
    pop_age_now,
    pop_sex_now,
    group_target,
    sa4_match,
) -> dict:
    def annual(state: str, year: int) -> int:
        return sum(by_state[state].get((year, month), 0) for month in range(1, 13))

    def part_year(state: str, year: int, last_month: int) -> int:
        return sum(by_state[state].get((year, month), 0) for month in range(1, last_month + 1))

    def latest(state: str) -> int:
        total = 0
        year, month = WINDOW_START
        while (year, month) <= (LATEST_YEAR, LATEST_MONTH):
            total += by_state[state].get((year, month), 0)
            if month == 12:
                year, month = year + 1, 1
            else:
                month += 1
        return total

    states_out = []
    for state in ("AUS", *STATES):
        deaths = latest(state)
        target = baseline[state] * factor_now
        target_rounded = round_people(target)
        population = pop_now[state]
        rate = deaths / population * 100_000
        full_year_target = round_people(baseline[state] * trajectory_factor(LATEST_YEAR, 12))
        states_out.append(
            {
                "code": state,
                "name": STATE_NAMES[state],
                "deaths": deaths,
                "baseline": baseline[state],
                "target_unrounded": target,
                "target": target_rounded,
                "excess": deaths - target_rounded,
                "population": population,
                "rate": rate,
                "full_year_target": full_year_target,
                "year_to_september": part_year(state, LATEST_YEAR, LATEST_MONTH),
            }
        )

    aus = next(item for item in states_out if item["code"] == "AUS")
    streak = _rise_streak(annual)
    jan_sep_history = {year: part_year("AUS", year, 9) for year in range(1989, LATEST_YEAR + 1)}
    higher_years = [year for year, deaths in jan_sep_history.items() if year < LATEST_YEAR and deaths > jan_sep_history[LATEST_YEAR]]
    highest_since = max(higher_years) if higher_years else None

    wa_sep = {year: _rolling_to_september(by_state["WA"], year) for year in range(1990, LATEST_YEAR + 1)}
    wa_now = wa_sep[LATEST_YEAR]
    wa_higher = [year for year, deaths in wa_sep.items() if year < LATEST_YEAR and deaths > wa_now]

    slices = _slice_payload(con, pop_now, pop_age_now, pop_sex_now)
    holiday = _holiday_payload(by_state, baseline, factor_now)
    calendar_years = []
    for year in range(2016, LATEST_YEAR):
        deaths = annual("AUS", year)
        calendar_years.append(
            {
                "year": year,
                "deaths": deaths,
                "previous": annual("AUS", year - 1),
                "change": deaths - annual("AUS", year - 1),
            }
        )

    series = _chart_series(by_state, baseline)
    vic = _victoria_payload(con)
    children = con.execute(
        """
        SELECT count(*) FROM fatalities
        WHERE age BETWEEN 0 AND 7
          AND ((year = 2025 AND month >= 10) OR (year = 2026 AND month <= 9))
        """
    ).fetchone()[0]
    unknown_age = con.execute(
        """
        SELECT count(*) FROM fatalities
        WHERE age IS NULL
          AND ((year = 2025 AND month >= 10) OR (year = 2026 AND month <= 9))
        """
    ).fetchone()[0]

    return {
        "as_of": "2026-09-30",
        "population_quarter": "2026-Q1",
        "factor_now": factor_now,
        "model_factor": mean_year_end_factor(MODEL_YEARS),
        "australia": aus,
        "states": states_out,
        "streak": streak,
        "calendar_years": calendar_years,
        "jan_sep": jan_sep_history[LATEST_YEAR],
        "jan_sep_previous": jan_sep_history[LATEST_YEAR - 1],
        "jan_sep_highest_since": highest_since,
        "jan_sep_history_from": 1989,
        "wa_12m": wa_now,
        "wa_higher_year": max(wa_higher) if wa_higher else None,
        "children_0_7": int(children),
        "unknown_age": int(unknown_age),
        "slices": slices,
        "holiday": holiday,
        "series": series,
        "victoria": vic,
        "sa4_names_matched": sa4_match[0],
        "sa4_names_known": sa4_match[1],
        "group_target_rate": {
            f"{state}|{group}": rates for (state, group), rates in group_target.items()
        },
    }


def _rise_streak(annual) -> dict:
    rises = []
    for year in range(2016, LATEST_YEAR):
        if annual("AUS", year) > annual("AUS", year - 1):
            rises.append(year)
        else:
            rises = []
    return {
        "rising_full_years": rises,
        "partial_2026_above_2025_same_months": False,
        "completed_rises": len(rises),
    }


def _rolling_to_september(monthly: dict, year: int) -> int:
    total = 0
    cursor_year, cursor_month = year - 1, 10
    while (cursor_year, cursor_month) <= (year, 9):
        total += monthly.get((cursor_year, cursor_month), 0)
        if cursor_month == 12:
            cursor_year, cursor_month = cursor_year + 1, 1
        else:
            cursor_month += 1
    return total


def _slice_payload(con, pop_now, pop_age_now, pop_sex_now) -> dict:
    rows = con.execute(
        """
        SELECT slice_type, state, slice_value, baseline_annual, recent_deaths, target_deaths, excess_deaths
        FROM slice_latest
        """
    ).fetchall()
    payload: dict[str, list] = defaultdict(list)
    for slice_type, state, value, baseline_annual, recent, target, excess in rows:
        if value in (None, "Unknown", ""):
            continue
        item = {
            "state": state,
            "label": value,
            "baseline": float(baseline_annual or 0),
            "deaths": float(recent or 0),
            "target": None if target is None else float(target),
            "excess": None if excess is None else float(excess),
        }
        if slice_type == "road_user" and value in ROAD_USERS:
            population = pop_now.get(state)
            if population:
                item["rate"] = item["deaths"] / population * 100_000
            item["vulnerable"] = value in VULNERABLE
        if slice_type == "age_band" and value in AGE_BANDS:
            population = pop_age_now.get(value, {}).get(state)
            if population:
                item["rate"] = item["deaths"] / population * 100_000
                item["population"] = population
        if slice_type == "gender" and value in ("Male", "Female"):
            population = pop_sex_now.get((state, value))
            if population:
                item["rate"] = item["deaths"] / population * 100_000
                item["population"] = population
        payload[slice_type].append(item)
    return payload


def _holiday_payload(by_state, baseline, factor_now) -> dict:
    def month_deaths(state, year, month):
        return by_state[state].get((year, month), 0)

    seasons = []
    for january_year in range(2018, 2027):
        december_year = january_year - 1
        if january_year == 2026:
            # Included: December 2025 and January 2026 are both inside the published file.
            pass
        deaths = month_deaths("AUS", december_year, 12) + month_deaths("AUS", january_year, 1)
        seasons.append({"january_year": january_year, "deaths": deaths, "days": 62})

    baseline_seasons = [item["deaths"] for item in seasons if item["january_year"] in (2018, 2019, 2020)]
    # Those january years mix Dec of the previous year. Also report calendar Dec+Jan inside baseline years.
    baseline_calendar = []
    for year in BASELINE_YEARS:
        baseline_calendar.append(month_deaths("AUS", year, 12) + month_deaths("AUS", year, 1))
    latest = next(item for item in seasons if item["january_year"] == 2026)
    baseline_avg = sum(baseline_calendar) / len(baseline_calendar)

    # Per-day comparison on complete years 2022-2025.
    holiday_deaths = 0
    holiday_days = 0
    other_deaths = 0
    other_days = 0
    for year in range(2022, 2026):
        for month in range(1, 13):
            deaths = month_deaths("AUS", year, month)
            days = days_in_month(year, month)
            if month in (12, 1):
                holiday_deaths += deaths
                holiday_days += days
            else:
                other_deaths += deaths
                other_days += days

    by_state_latest = []
    for state in STATES:
        latest_state = by_state[state].get((2025, 12), 0) + by_state[state].get((2026, 1), 0)
        base_vals = [
            by_state[state].get((year, 12), 0) + by_state[state].get((year, 1), 0) for year in BASELINE_YEARS
        ]
        base_avg = sum(base_vals) / len(base_vals)
        target = base_avg * factor_now
        by_state_latest.append(
            {
                "state": state,
                "name": STATE_NAMES[state],
                "deaths": latest_state,
                "baseline": base_avg,
                "target": round_people(target),
                "excess": latest_state - round_people(target),
            }
        )

    return {
        "latest_season": "December 2025 and January 2026",
        "latest_deaths": latest["deaths"],
        "baseline_average": baseline_avg,
        "baseline_note": "Average of December plus January deaths in 2018, 2019 and 2020",
        "target": round_people(baseline_avg * factor_now),
        "excess": latest["deaths"] - round_people(baseline_avg * factor_now),
        "per_day_holiday": holiday_deaths / holiday_days,
        "per_day_other": other_deaths / other_days,
        "per_day_years": "2022–2025",
        "states": by_state_latest,
        "seasons": seasons,
        "ignored_baseline_season_mix": baseline_seasons,
    }


def _chart_series(by_state, baseline) -> dict:
    series = {}
    for state in ("AUS", *STATES):
        points_actual = []
        points_path = []
        year, month = 2018, 1
        history = []
        cursor_y, cursor_m = 2010, 1
        lookup = []
        while (cursor_y, cursor_m) <= (2030, 12):
            lookup.append((cursor_y, cursor_m))
            if cursor_m == 12:
                cursor_y, cursor_m = cursor_y + 1, 1
            else:
                cursor_m += 1
        values = [by_state[state].get(key, 0) for key in lookup]
        rolls = _rolling(values)
        index = {(y, m): i for i, (y, m) in enumerate(lookup)}
        while (year, month) <= (2030, 12):
            label = f"{year}-{month:02d}"
            roll = rolls[index[(year, month)]]
            factor = trajectory_factor(year, month) if (year, month) >= (2020, 12) else None
            if (year, month) <= (LATEST_YEAR, LATEST_MONTH) and roll is not None:
                points_actual.append({"x": label, "y": roll})
            if factor is not None:
                points_path.append({"x": label, "y": baseline[state] * factor})
            if month == 12:
                year, month = year + 1, 1
            else:
                month += 1
        series[state] = {"actual": points_actual, "path": points_path}
        _ = history
    return series


def _victoria_payload(con) -> dict:
    speed = con.execute(
        """
        SELECT
          CASE
            WHEN speed_limit IS NULL OR speed_limit IN (777, 888, 999) THEN 'Unknown'
            WHEN speed_limit <= 50 THEN 'Up to 50 km/h'
            WHEN speed_limit <= 80 THEN '60–80 km/h'
            ELSE '90 km/h and above'
          END AS band,
          count(*) AS crashes,
          count(*) FILTER (WHERE severity = 'Fatal accident') AS fatal_crashes,
          sum(deaths) AS deaths
        FROM vic_crashes
        WHERE year BETWEEN 2018 AND 2025
        GROUP BY 1
        ORDER BY 1
        """
    ).fetchall()
    light = con.execute(
        """
        SELECT
          CASE
            WHEN light_condition = 'Day' THEN 'Daylight'
            WHEN light_condition IS NULL OR light_condition = '' THEN 'Unknown'
            ELSE 'Dusk, dawn or dark'
          END AS band,
          count(*) FILTER (WHERE severity = 'Fatal accident') AS fatal_crashes,
          count(*) AS crashes
        FROM vic_crashes
        WHERE year BETWEEN 2018 AND 2025
        GROUP BY 1
        """
    ).fetchall()
    place = con.execute(
        """
        SELECT
          CASE
            WHEN road_geometry IN ('Cross intersection', 'T intersection', 'Y intersection', 'Multiple intersections')
              THEN 'At an intersection'
            WHEN road_geometry = 'Not at intersection' THEN 'Not at an intersection'
            ELSE 'Other or unknown'
          END AS band,
          count(*) FILTER (WHERE severity = 'Fatal accident') AS fatal_crashes
        FROM vic_crashes
        WHERE year BETWEEN 2018 AND 2025
        GROUP BY 1
        """
    ).fetchall()
    # The source spells T intersection with quotes. Match both.
    if all(row[1] == 0 or row[0] == "Other or unknown" for row in place):
        place = con.execute(
            """
            SELECT
              CASE
                WHEN lower(road_geometry) LIKE '%intersection%' AND lower(road_geometry) NOT LIKE 'not at%'
                  THEN 'At an intersection'
                WHEN lower(road_geometry) LIKE 'not at intersection%' THEN 'Not at an intersection'
                ELSE 'Other or unknown'
              END AS band,
              count(*) FILTER (WHERE severity = 'Fatal accident') AS fatal_crashes
            FROM vic_crashes
            WHERE year BETWEEN 2018 AND 2025
            GROUP BY 1
            """
        ).fetchall()
    regions = con.execute(
        """
        SELECT coalesce(nullif(dtp_region, ''), 'Unspecified') AS region,
               count(*) FILTER (WHERE severity = 'Fatal accident') AS fatal_crashes,
               sum(deaths) AS deaths
        FROM vic_crashes
        WHERE year BETWEEN 2023 AND 2025
        GROUP BY 1
        ORDER BY fatal_crashes DESC
        """
    ).fetchall()
    holiday_light = con.execute(
        """
        SELECT
          CASE WHEN light_condition = 'Day' THEN 'Daylight' ELSE 'Dusk, dawn or dark' END AS band,
          count(*) FILTER (WHERE severity = 'Fatal accident') AS fatal_crashes
        FROM vic_crashes
        WHERE year BETWEEN 2018 AND 2025 AND month IN (12, 1)
        GROUP BY 1
        """
    ).fetchall()
    coverage = con.execute(
        "SELECT min(accident_date)::VARCHAR, max(accident_date)::VARCHAR, count(*) FROM vic_crashes"
    ).fetchone()
    return {
        "coverage_start": coverage[0],
        "coverage_end": coverage[1],
        "rows": int(coverage[2]),
        "note": "Victoria's open crash file is fatal and serious-injury crashes. 2026 is only partial, so comparisons stop at 2025. These are not the same numbers as the national death count.",
        "speed": [
            {"label": band, "crashes": int(crashes), "fatal_crashes": int(fatal), "deaths": int(deaths or 0)}
            for band, crashes, fatal, deaths in speed
        ],
        "light": [
            {"label": band, "fatal_crashes": int(fatal), "crashes": int(crashes)} for band, fatal, crashes in light
        ],
        "place": [{"label": band, "fatal_crashes": int(fatal)} for band, fatal in place],
        "regions": [
            {"label": region.title() if region != "Unspecified" else region, "fatal_crashes": int(fatal), "deaths": int(deaths or 0)}
            for region, fatal, deaths in regions
        ],
        "holiday_light": [{"label": band, "fatal_crashes": int(fatal)} for band, fatal in holiday_light],
    }


def build_pipeline() -> dict:
    reset_warehouse()
    build_crashes()
    build_population()
    facts = build_targets_and_serving()
    # The rise-streak helper above is completed here from the stored calendar, so the
    # published fact does not depend on the placeholder.
    facts["streak"]["partial_2026_above_2025_same_months"] = facts["jan_sep"] > facts["jan_sep_previous"]
    FACTS_PATH.write_text(json.dumps(facts, indent=2) + "\n")
    aus = facts["australia"]
    print(
        f"12 months to September 2026: {aus['deaths']} deaths, "
        f"path {aus['target']}, {aus['excess']} above the path."
    )
    return facts
