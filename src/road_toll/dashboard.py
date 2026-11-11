"""Stakeholder pages. Plain language only; the method lives in INSIGHTS.md."""

from __future__ import annotations

import json
import shutil
from datetime import datetime
from zoneinfo import ZoneInfo

import duckdb

from road_toll.model import clear_hotspots
from road_toll.paths import (
    DB_PATH,
    FACTS_PATH,
    HOTSPOTS_PATH,
    SITE,
    SNAPSHOT,
    STATE_NAMES,
    STATES,
)

SYDNEY = ZoneInfo("Australia/Sydney")


def _n(value: float | int) -> str:
    return f"{int(round(value)):,}"


def _rate(value: float) -> str:
    return f"{value:.1f}"


def _slice(facts: dict, kind: str, state: str) -> list[dict]:
    rows = [row for row in facts["slices"].get(kind, []) if row["state"] == state]
    return rows


def _hours(con: duckdb.DuckDBPyConnection) -> dict[str, list[dict]]:
    rows = con.execute(
        """
        SELECT state, hour, count(*)::INTEGER AS deaths
        FROM fatalities_all
        WHERE hour IS NOT NULL
          AND ((year = 2025 AND month >= 10) OR (year = 2026 AND month <= 9))
        GROUP BY 1, 2
        ORDER BY 1, 2
        """
    ).fetchall()
    grouped: dict[str, list[dict]] = {state: [] for state in ("AUS", *STATES)}
    for state, hour, deaths in rows:
        grouped.setdefault(state, []).append({"hour": int(hour), "deaths": int(deaths)})
    return grouped


def _annual(con: duckdb.DuckDBPyConnection) -> dict[str, list[dict]]:
    rows = con.execute(
        """
        SELECT state, year, count(*)::INTEGER AS deaths
        FROM fatalities_all
        WHERE year BETWEEN 2016 AND 2025
        GROUP BY 1, 2
        ORDER BY 1, 2
        """
    ).fetchall()
    grouped: dict[str, list[dict]] = {}
    for state, year, deaths in rows:
        grouped.setdefault(state, []).append({"year": int(year), "deaths": int(deaths)})
    return grouped


def _kpis(facts: dict, state: str) -> list[dict]:
    row = next(item for item in facts["states"] if item["code"] == state)
    name = "Australia" if state == "AUS" else row["name"]
    holiday = next(item for item in facts["holiday"]["states"] if item["state"] == state) if state != "AUS" else None
    cards = [
        {
            "label": "Deaths in the last 12 months",
            "value": _n(row["deaths"]),
            "unit": "people",
            "status": "bad",
            "status_label": "Off track",
            "takeaway": f"{_n(row['excess'])} more than the path allows for {name}.",
        },
        {
            "label": "Path for those 12 months",
            "value": _n(row["target"]),
            "unit": "deaths allowed",
            "status": "watch",
            "status_label": "2030 path",
            "takeaway": "Half the 2018–2020 toll, stepped down evenly to 2030.",
        },
        {
            "label": "Deaths per 100,000 people",
            "value": _rate(row["rate"]),
            "unit": "per year",
            "status": "watch" if state == "AUS" else ("bad" if row["rate"] >= 5 else "ok"),
            "status_label": "National rate" if state == "AUS" else ("Above 5 per 100,000" if row["rate"] >= 5 else "Under 5 per 100,000"),
            "takeaway": "Uses the population estimate at 31 March 2026.",
        },
        {
            "label": "January to September 2026",
            "value": _n(row["year_to_september"]),
            "unit": "deaths so far",
            "status": "bad" if row["year_to_september"] > row["full_year_target"] else "watch",
            "status_label": "Past the full-year ceiling"
            if row["year_to_september"] > row["full_year_target"]
            else "Still under the full-year ceiling",
            "takeaway": f"The full-year ceiling for 2026 is {_n(row['full_year_target'])}.",
        },
    ]
    if state == "AUS":
        cards.append(
            {
                "label": "Children aged 0–7",
                "value": _n(facts["children_0_7"]),
                "unit": "deaths",
                "status": "bad",
                "status_label": "Not at zero",
                "takeaway": "The national aim is no deaths of children 7 and under by 2030.",
            }
        )
    elif holiday is not None:
        cards.append(
            {
                "label": "Last summer, December and January",
                "value": _n(holiday["deaths"]),
                "unit": "deaths",
                "status": "bad" if holiday["excess"] > 0 else "ok",
                "status_label": "Above the summer path" if holiday["excess"] > 0 else "On the summer path",
                "takeaway": f"{_n(holiday['excess'])} compared with the summer path of {_n(holiday['target'])}.",
            }
        )
    return cards


def _line(facts: dict, state: str) -> dict:
    series = facts["series"][state]
    actual = series["actual"]
    path = series["path"]
    latest = actual[-1]
    end = path[-1]
    return {
        "kind": "line",
        "title": "The toll is above the line that gets to half by 2030",
        "insight": (
            f"The latest 12 months ended at {_n(latest['y'])} deaths. "
            f"The line ends at {_n(end['y'])} in December 2030."
        ),
        "series": [
            {"name": "Deaths, last 12 months", "color": "#ff5d73", "points": actual, "dash": False},
            {"name": "Path to half by 2030", "color": "#3ee0a0", "points": path, "dash": True},
        ],
        "exact": (
            f"Exact values: {_n(latest['y'])} deaths in the 12 months to September 2026. "
            f"Unrounded path that month: {facts['states'][[s['code'] for s in facts['states']].index(state)]['target_unrounded']:.1f}."
        ),
    }


def _bars(title: str, insight: str, rows: list[dict], exact: str, color: str = "#4cc9f0") -> dict:
    return {
        "kind": "bars",
        "title": title,
        "insight": insight,
        "color": color,
        "rows": rows,
        "exact": exact,
    }


def _columns(title: str, insight: str, rows: list[dict], exact: str) -> dict:
    return {"kind": "columns", "title": title, "insight": insight, "rows": rows, "exact": exact}


def _top(rows: list[dict], key: str = "excess", limit: int = 8) -> list[dict]:
    ranked = sorted(rows, key=lambda row: -(row.get(key) or 0))
    return ranked[:limit]


def _view(facts: dict, state: str, annual: dict, hours: dict, hotspots: list[dict]) -> dict:
    users = [row for row in _slice(facts, "road_user", state) if row["label"] != "Unknown"]
    ages = [row for row in _slice(facts, "age_band", state)]
    speeds = [row for row in _slice(facts, "speed_band", state) if row["label"] != "Unknown"]
    places = [row for row in _slice(facts, "remoteness", state) if row["label"] != "Unknown"]
    roads = [row for row in _slice(facts, "road_type", state) if row["label"] != "Unknown"]
    days = [row for row in _slice(facts, "day_of_week", state)]
    genders = [row for row in _slice(facts, "gender", state) if row["label"] in {"Male", "Female"}]
    order = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]
    days.sort(key=lambda row: order.index(row["label"]) if row["label"] in order else 9)
    age_order = ["0–7", "8–16", "17–25", "26–39", "40–64", "65–74", "75+"]
    ages.sort(key=lambda row: age_order.index(row["label"]) if row["label"] in age_order else 9)

    user_rows = [
        {
            "label": row["label"],
            "value": row["excess"],
            "display": f"{_n(row['deaths'])} deaths, {_n(row['excess'])} above the path",
        }
        for row in _top(users, "excess", 5)
    ]
    age_rows = [
        {
            "label": row["label"],
            "value": row.get("rate") or 0,
            "display": f"{_rate(row.get('rate') or 0)} per 100,000 · {_n(row['deaths'])} deaths",
        }
        for row in ages
    ]
    gender_rows = [
        {
            "label": "Men" if row["label"] == "Male" else "Women",
            "value": row.get("rate") or 0,
            "display": f"{_rate(row.get('rate') or 0)} per 100,000 · {_n(row['deaths'])} deaths",
        }
        for row in genders
    ]
    speed_rows = [
        {"label": row["label"], "value": row["deaths"], "display": f"{_n(row['deaths'])} deaths, {_n(row['excess'])} above the path"}
        for row in speeds
    ]
    place_rows = [
        {"label": row["label"], "value": row["deaths"], "display": f"{_n(row['deaths'])} deaths, {_n(row['excess'])} above the path"}
        for row in _top(places, "deaths", 5)
    ]
    road_rows = [
        {"label": row["label"], "value": row["excess"], "display": f"{_n(row['deaths'])} deaths, {_n(row['excess'])} above the path"}
        for row in _top(roads, "excess", 5)
    ]
    day_rows = [
        {"label": row["label"], "value": row["deaths"], "display": f"{_n(row['deaths'])} deaths"}
        for row in days
    ]
    hour_rows = [{"label": f"{row['hour']}", "value": row["deaths"], "display": f"{_n(row['deaths'])}"} for row in hours.get(state, [])]
    year_rows = [{"label": str(row["year"]), "value": row["deaths"], "display": _n(row["deaths"])} for row in annual.get(state, [])]

    local_hotspots = [row for row in hotspots if state == "AUS" or row["state"] == state]
    local_hotspots = sorted(local_hotspots, key=lambda row: -row["excess_per_year"])[:6]
    hot_rows = [
        {
            "label": f"{row['sa4_name']} · {row['road_user_group'].lower()}s",
            "value": row["excess_per_year"],
            "display": (
                f"about {_rate(row['rate_low'])} to {_rate(row['rate_high'])} per 100,000, "
                f"against {_rate(row['state_rate_median'])} for the rest of the state"
            ),
        }
        for row in local_hotspots
    ]

    state_gap = [
        {
            "label": row["code"],
            "value": row["excess"],
            "display": f"{_n(row['deaths'])} deaths, {_n(row['excess'])} above the path, {_rate(row['rate'])} per 100,000",
        }
        for row in facts["states"]
        if row["code"] != "AUS"
    ]
    holiday_states = [
        {
            "label": row["state"],
            "value": row["excess"],
            "display": f"{_n(row['deaths'])} deaths last summer, {_n(row['excess'])} above the summer path",
        }
        for row in sorted(facts["holiday"]["states"], key=lambda item: -item["excess"])
    ]

    who_lead = max(users, key=lambda row: row["excess"])
    sharp = max(users, key=lambda row: (row["deaths"] / row["baseline"]) if row["baseline"] else 0)
    return {
        "toll": {
            "kpis": _kpis(facts, state),
            "hero": _line(facts, state),
            "charts": [
                _columns(
                    "Each full year since 2020 has been higher than the one before",
                    "2021 through 2025 rose every year. January to September 2026 is already above the same months of 2025.",
                    year_rows,
                    "Exact values: " + ", ".join(f"{row['label']} {row['display']}" for row in year_rows) + ". 2026 is not shown as a full year.",
                ),
                _bars(
                    "Queensland has the most lives above the path" if state == "AUS" else "Every state is above its own path",
                    "Colour on the map uses the same gap: extra deaths, not the rate per person.",
                    state_gap,
                    "Exact values: " + ", ".join(f"{row['label']} {row['display']}" for row in state_gap),
                    color="#ff5d73",
                ),
            ],
            "means": _means_toll(facts, state),
        },
        "where": {
            "kpis": _kpis(facts, state)[:4],
            "hero": {"kind": "map", "title": "Where the extra deaths are", "insight": "Darker states have more deaths above the path. The Northern Territory is the highest risk per person even though the gap in lives is smaller.", "exact": "Exact values are listed beside the map."},
            "charts": [
                _bars(
                    "Communities where the toll is higher than the rest of their state",
                    "These places stay high even after allowing for a small population and a few bad crashes. Wide ranges were left off this list.",
                    hot_rows or [{"label": "No clear hotspot in this filter", "value": 0, "display": "The range still overlaps the state."}],
                    "Exact values: " + "; ".join(f"{row['label']}: {row['display']}" for row in hot_rows),
                    color="#ffb020",
                ),
                _bars(
                    "Cities still hold the most deaths. Regional roads are close behind.",
                    "Major cities and inner regional areas account for most people killed in the last 12 months.",
                    place_rows,
                    "Exact values: " + ", ".join(f"{row['label']} {row['display']}" for row in place_rows),
                ),
                _bars(
                    "Local streets have moved furthest above their old level",
                    "Highways still kill more people. Local streets have the larger gap against their own path.",
                    road_rows,
                    "Exact values: " + ", ".join(f"{row['label']} {row['display']}" for row in road_rows),
                    color="#b794f6",
                ),
            ],
            "means": _means_where(facts, local_hotspots),
        },
        "who": {
            "kpis": _kpis(facts, state)[:3]
            + [
                {
                    "label": "Largest gap by road user",
                    "value": who_lead["label"],
                    "unit": f"{_n(who_lead['excess'])} above the path",
                    "status": "bad",
                    "status_label": "Most extra deaths",
                    "takeaway": f"{sharp['label']}s have the sharpest rise against their own path.",
                }
            ],
            "hero": _bars(
                "Drivers are the largest gap. People outside cars have the sharpest rise.",
                "Motorcyclists, pedestrians and cyclists are each far above the path set from their own 2018–2020 toll.",
                user_rows,
                "Exact values: " + ", ".join(f"{row['label']} {row['display']}" for row in user_rows),
                color="#ff8a5b",
            ),
            "charts": [
                _bars(
                    "Older people and young adults have the highest risk per person",
                    "The 40–64 group has the most extra deaths because it is the largest group of adults.",
                    age_rows,
                    "Exact values: " + ", ".join(f"{row['label']} {row['display']}" for row in age_rows),
                    color="#4cc9f0",
                ),
                _bars(
                    "Men die at nearly three times the rate of women",
                    "Rates use the male and female population at 31 March 2026.",
                    gender_rows,
                    "Exact values: " + ", ".join(f"{row['label']} {row['display']}" for row in gender_rows),
                    color="#ffd166",
                ),
            ],
            "means": _means_who(facts, users),
        },
        "when": {
            "kpis": _kpis(facts, state)[:4],
            "hero": _columns(
                "The deadliest hour is late afternoon, not the middle of the night",
                "Deaths peak around 4pm. Evening hours stay high, but the afternoon commute is the single worst hour.",
                hour_rows,
                "Exact values: " + ", ".join(f"{row['label']}:00 {_n(row['value'])}" for row in hour_rows),
            ),
            "charts": [
                _bars(
                    "The rise is across the week, not only the weekend",
                    "Saturday and Friday still have high counts. Monday has moved furthest above its old level.",
                    day_rows,
                    "Exact values: " + ", ".join(f"{row['label']} {row['display']}" for row in day_rows),
                ),
                _bars(
                    "60–80 km/h roads and high-speed roads both need the money",
                    "High-speed roads kill the most people. 60–80 km/h roads have the bigger gap against their path.",
                    speed_rows,
                    "Exact values: " + ", ".join(f"{row['label']} {row['display']}" for row in speed_rows),
                    color="#ff5d73",
                ),
            ],
            "means": _means_when(facts),
        },
        "holiday": {
            "kpis": [
                {
                    "label": "Last summer",
                    "value": _n(facts["holiday"]["latest_deaths"]),
                    "unit": "deaths in Dec 2025 and Jan 2026",
                    "status": "bad",
                    "status_label": "Above the summer path",
                    "takeaway": f"{_n(facts['holiday']['excess'])} above a summer path of {_n(facts['holiday']['target'])}.",
                },
                {
                    "label": "Deaths per day in summer",
                    "value": f"{facts['holiday']['per_day_holiday']:.1f}",
                    "unit": "December and January, 2022–2025",
                    "status": "watch",
                    "status_label": "Same daily pace",
                    "takeaway": f"Other months averaged {facts['holiday']['per_day_other']:.1f} deaths a day.",
                },
                {
                    "label": "South Australia, Jan–Sep 2026",
                    "value": _n(next(s["year_to_september"] for s in facts["states"] if s["code"] == "SA")),
                    "unit": "already past the 2026 ceiling",
                    "status": "bad",
                    "status_label": "Ceiling passed",
                    "takeaway": f"The full-year ceiling is {_n(next(s['full_year_target'] for s in facts['states'] if s['code'] == 'SA'))}.",
                },
                {
                    "label": "Western Australia, last 12 months",
                    "value": _n(facts["wa_12m"]),
                    "unit": "worst since 2010",
                    "status": "bad",
                    "status_label": "15-year high",
                    "takeaway": "No September-ending year since 2010 was higher.",
                },
            ],
            "hero": _bars(
                "Put the summer operation where the summer gap is widest",
                "Victoria, Queensland and New South Wales account for most of the extra summer deaths.",
                holiday_states,
                "Exact values: " + ", ".join(f"{row['label']} {row['display']}" for row in holiday_states),
                color="#ff8a5b",
            ),
            "charts": [
                {
                    "kind": "bars",
                    "title": "On faster Victorian roads, a serious crash is much more often fatal",
                    "insight": "Among Victoria's fatal and serious crashes in 2018–2025, about 9 in 100 on 90 km/h and faster roads were fatal, against about 2 in 100 on streets up to 50 km/h.",
                    "color": "#b794f6",
                    "rows": _vic_speed_rows(facts),
                    "exact": _vic_exact(facts),
                },
                _bars(
                    "Most Victorian fatal crashes are not at an intersection",
                    "Roadside and mid-block risk, not only black-spot intersections, is where the deaths are.",
                    [
                        {"label": row["label"], "value": row["fatal_crashes"], "display": f"{_n(row['fatal_crashes'])} fatal crashes"}
                        for row in facts["victoria"]["place"]
                        if row["label"] != "Other or unknown"
                    ],
                    "Exact values: "
                    + ", ".join(
                        f"{row['label']} {_n(row['fatal_crashes'])}"
                        for row in facts["victoria"]["place"]
                    ),
                    color="#4cc9f0",
                ),
            ],
            "means": _means_holiday(facts),
        },
    }


def _vic_speed_rows(facts: dict) -> list[dict]:
    rows = []
    for row in facts["victoria"]["speed"]:
        if row["label"] == "Unknown" or row["crashes"] == 0:
            continue
        share = row["fatal_crashes"] / row["crashes"]
        rows.append(
            {
                "label": row["label"],
                "value": share * 100,
                "display": f"{share * 100:.0f} in 100 fatal or serious crashes were fatal",
            }
        )
    return rows


def _vic_exact(facts: dict) -> str:
    parts = []
    for row in facts["victoria"]["speed"]:
        if row["label"] == "Unknown":
            continue
        parts.append(f"{row['label']}: {row['fatal_crashes']:,} fatal crashes out of {row['crashes']:,}")
    return "Exact values, Victoria 2018–2025: " + "; ".join(parts) + "."


def _means_toll(facts: dict, state: str) -> str:
    row = next(item for item in facts["states"] if item["code"] == state)
    if state == "AUS":
        return (
            f"In the 12 months to September 2026, {_n(row['deaths'])} people died on Australian roads. "
            f"The straight path to halving the 2018–2020 toll by 2030 allowed about {_n(row['target'])}. "
            f"That is {_n(row['excess'])} more deaths than the path. "
            "Each full year from 2021 to 2025 was higher than the year before, and January to September 2026 "
            f"({_n(facts['jan_sep'])}) is already above the same months of 2025 ({_n(facts['jan_sep_previous'])}). "
            f"It is the highest January–September toll since {facts['jan_sep_highest_since']}. "
            f"Those {_n(facts['jan_sep'])} deaths already pass the full-year ceiling for 2026, which is {_n(row['full_year_target'])}."
        )
    return (
        f"{row['name']} recorded {_n(row['deaths'])} deaths in the last 12 months, "
        f"{_n(row['excess'])} above its own path of {_n(row['target'])}, "
        f"or {_rate(row['rate'])} deaths per 100,000 people."
    )


def _means_where(facts: dict, hotspots: list[dict]) -> str:
    lead = hotspots[0]["sa4_name"] if hotspots else "regional Australia"
    nt = next(item for item in facts["states"] if item["code"] == "NT")
    qld = next(item for item in facts["states"] if item["code"] == "QLD")
    return (
        f"Queensland is furthest above its path ({_n(qld['excess'])} extra deaths). "
        f"The Northern Territory has the highest risk per person, {_rate(nt['rate'])} per 100,000. "
        f"Inside the states, {lead} stands out from its neighbours. "
        "Spend volume where the extra lives are — Queensland, New South Wales, Victoria and Western Australia — "
        "and intensity on regional roads and the Territory."
    )


def _means_who(facts: dict, users: list[dict]) -> str:
    by_name = {row["label"]: row for row in users}
    return (
        f"Drivers account for {_n(by_name['Driver']['excess'])} of the extra deaths. "
        f"Motorcyclists are {_n(by_name['Motorcyclist']['excess'])} above their path, "
        f"pedestrians {_n(by_name['Pedestrian']['excess'])}, and cyclists {_n(by_name['Cyclist']['excess'])}. "
        f"Cyclist deaths ({_n(by_name['Cyclist']['deaths'])}) are about double the path. "
        f"{_n(facts['children_0_7'])} children aged 0–7 died in the last 12 months. The aim for 2030 is none."
    )


def _means_when(facts: dict) -> str:
    return (
        "Schedule enforcement for the afternoon peak, especially 2pm to 6pm, and keep it going into the evening. "
        "Do not concentrate only on Saturday night. The weekday toll has risen against its old level. "
        "Pair that with speed management on 60–80 km/h arterials and on 90 km/h and faster roads."
    )


def _means_holiday(facts: dict) -> str:
    return (
        f"December and January are not deadlier per day than the rest of the year "
        f"({facts['holiday']['per_day_holiday']:.1f} deaths a day against {facts['holiday']['per_day_other']:.1f}). "
        f"They are still part of the miss: last summer was {_n(facts['holiday']['latest_deaths'])} deaths "
        f"against a path of {_n(facts['holiday']['target'])}. "
        "Use the summer blitz on the same places and people the whole year is failing: "
        "Queensland, New South Wales, Victoria and Western Australia; regional highways and wheat-belt roads; "
        "motorcyclists, pedestrians and cyclists; afternoons; and faster roads where a crash is more often fatal. "
        "South Australia has already passed its 2026 ceiling. Western Australia is having its worst 12 months since 2010."
    )


def write_insights(facts: dict, hotspots: list[dict]) -> None:
    aus = facts["australia"]
    states = {row["code"]: row for row in facts["states"]}
    users = {row["label"]: row for row in _slice(facts, "road_user", "AUS")}
    lines = [
        "# What the toll says, and where the summer money should go",
        "",
        f"Figures use BITRE deaths current to 30 September 2026 and the ABS population at 31 March 2026. "
        f"The path halves each state's 2018–2020 average by the end of 2030 in a straight line. "
        f"The national baseline is {aus['baseline']:.1f} deaths a year.",
        "",
        "## The decision",
        "",
        "Holiday-season enforcement and infrastructure money should go to four places at once:",
        "",
        f"1. **Queensland, New South Wales, Victoria and Western Australia**, in that order of lives above the path "
        f"({_n(states['QLD']['excess'])}, {_n(states['NSW']['excess'])}, {_n(states['VIC']['excess'])}, {_n(states['WA']['excess'])}). "
        f"Together they are most of the national gap of {_n(aus['excess'])}.",
        f"2. **Regional roads that sit above their own state**, led by Western Australia's Wheat Belt, "
        f"Queensland's Wide Bay and Darling Downs, Victoria's Hume corridor, and New South Wales' New England, Central West and Capital region. "
        f"The Northern Territory is the highest risk per person at {_rate(states['NT']['rate'])} deaths per 100,000, "
        f"against {_rate(aus['rate'])} nationally.",
        f"3. **People outside cars, without dropping drivers.** Drivers are still the largest gap "
        f"({_n(users['Driver']['excess'])} above the path, {_n(users['Driver']['deaths'])} deaths). "
        f"Motorcyclists ({_n(users['Motorcyclist']['excess'])} above the path), "
        f"pedestrians ({_n(users['Pedestrian']['excess'])}) and cyclists ({_n(users['Cyclist']['excess'])}) "
        f"have the sharpest rises. Cyclist deaths are about double the path.",
        "4. **Afternoons and faster roads.** The worst hour in the last 12 months is 4pm (93 deaths). "
        "In Victoria, about 9 in 100 fatal-or-serious crashes on 90 km/h and faster roads were fatal, "
        "against about 2 in 100 on streets up to 50 km/h. Most Victorian fatal crashes were not at an intersection.",
        "",
        "South Australia does not have the largest gap, but it has already failed 2026: "
        f"84 deaths from January to September, against a full-year ceiling of {states['SA']['full_year_target']}. "
        f"Western Australia's latest 12-month toll is {facts['wa_12m']}, its worst September-ending year since 2010.",
        "",
        "## The national miss",
        "",
        f"The 12 months to September 2026: **{_n(aus['deaths'])} deaths**. "
        f"The path allowed **{_n(aus['target'])}** (unrounded {aus['target_unrounded']:.1f}). "
        f"**{_n(aus['excess'])} deaths above the path.** Rate: **{_rate(aus['rate'])} per 100,000**.",
        "",
        "Full calendar years rose every year from 2021 to 2025 "
        "(1,129, 1,182, 1,252, 1,291, 1,306). "
        f"January–September 2026 was {_n(facts['jan_sep'])}, above {_n(facts['jan_sep_previous'])} in the same months of 2025. "
        f"That is the sixth year of the rise, and the highest January–September total since {facts['jan_sep_highest_since']}. "
        f"It is already above the full-year ceiling for 2026 ({_n(aus['full_year_target'])}).",
        "",
        f"Children aged 0–7: **{facts['children_0_7']} deaths** in the last 12 months. The 2030 aim is zero. "
        f"Age was missing for {facts['unknown_age']} of the latest deaths, so age rates exclude them.",
        "",
        "Men died at 7.1 per 100,000 and women at 2.5. Men are about three quarters of the toll.",
        "",
        "People 75 and over (7.3 per 100,000) and people 17–25 (7.1) have the highest age-specific rates. "
        "People 40–64 contribute the most extra deaths because the group is large.",
        "",
        "## Summer is not a different crash",
        "",
        f"Over 2022–2025, December and January averaged {facts['holiday']['per_day_holiday']:.2f} deaths a day "
        f"and the other months averaged {facts['holiday']['per_day_other']:.2f}. "
        "Summer is not a spike in daily risk. It is when agencies already put people on the road, "
        f"and last summer (December 2025 and January 2026) was still {facts['holiday']['latest_deaths']} deaths "
        f"against a path of {facts['holiday']['target']}.",
        "",
        "Use that operation on the year-round gaps above. Do not design it as if only holiday weekends kill.",
        "",
        "## Communities, and how sure we are",
        "",
        "Deaths in a single community bounce around. A model shares strength across communities in the same state "
        "and across road-user groups, using deaths and population for 2022–2025. "
        "A place is listed only when at least five people died in that group over those four years "
        "and the likely range for its rate sits entirely above the state rate.",
        "",
        "The clearest extra deaths per year are regional driver tolls: "
        + "; ".join(
            f"{row['sa4_name']} ({row['state']}, {row['road_user_group'].lower()}, "
            f"about {row['excess_per_year']:.0f} extra deaths a year)"
            for row in hotspots[:8]
        )
        + ".",
        "",
        "The check on the model: it expected about the same total deaths as actually occurred "
        "across those communities and groups, and the share of community-years with no deaths was close. "
        "Details are in `data/model/ppc.json` after `make model`.",
        "",
        "## What this does not show",
        "",
        "There is no measure here of kilometres driven, registered motorcycles, or hours of enforcement, "
        "so a high rate is not proof of why the risk is high. Recent months will be revised. "
        "Victoria's crash file is a different collection and stops being complete in 2025; "
        "it is used for road environment, not for the national death total. "
        "About 100 of the latest deaths have no remoteness area recorded. "
        "The state paths apply the national halving rule to each state. The Strategy itself sets a national target.",
        "",
    ]
    (FACTS_PATH.parent.parent.parent / "INSIGHTS.md").write_text("\n".join(lines) + "\n")


def build_dashboard() -> None:
    facts = json.loads(FACTS_PATH.read_text())
    hotspots = clear_hotspots() if HOTSPOTS_PATH.exists() else []
    con = duckdb.connect(str(DB_PATH), read_only=True)
    annual = _annual(con)
    hours = _hours(con)
    con.close()
    generated = datetime.now(SYDNEY)
    payload = {
        "meta": {
            "data_current": "30 September 2026, Australian Eastern Standard Time",
            "generated": generated.strftime("%-d %B %Y, %-I:%M %p %Z"),
            "population": "Rates use the Australian Bureau of Statistics population at 31 March 2026.",
            "revision": "Deaths in recent months are preliminary and are revised as reports are finished.",
        },
        "map": [
            {
                "code": row["code"],
                "name": row["name"],
                "deaths": row["deaths"],
                "excess": row["excess"],
                "rate": round(row["rate"], 2),
                "target": row["target"],
            }
            for row in facts["states"]
            if row["code"] != "AUS"
        ],
        "states": [{"code": "AUS", "name": "Australia"}]
        + [{"code": code, "name": STATE_NAMES[code]} for code in STATES],
        "views": {
            state: _view(facts, state, annual, hours, hotspots) for state in ("AUS", *STATES)
        },
    }
    data_dir = SITE / "data"
    data_dir.mkdir(parents=True, exist_ok=True)
    (data_dir / "dashboard.json").write_text(json.dumps(payload))
    shutil.copyfile(SNAPSHOT / "aus_states.geojson", data_dir / "aus-states.geojson")
    write_insights(facts, hotspots)
    print(f"Dashboard written to {SITE}")
