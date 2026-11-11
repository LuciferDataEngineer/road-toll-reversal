import json

from road_toll.paths import FACTS_PATH
from road_toll.pipeline import build_pipeline


def facts() -> dict:
    if not FACTS_PATH.exists():
        build_pipeline()
    return json.loads(FACTS_PATH.read_text())


def test_latest_twelve_months_match_the_published_toll():
    australia = facts()["australia"]
    assert australia["deaths"] == 1334
    assert australia["excess"] > 400
    assert australia["target"] < australia["deaths"]


def test_every_state_is_above_its_path_and_south_australia_has_passed_2026():
    rows = {row["code"]: row for row in facts()["states"]}
    assert all(row["excess"] > 0 for row in rows.values())
    assert rows["SA"]["year_to_september"] > rows["SA"]["full_year_target"]
    assert rows["NT"]["rate"] == max(row["rate"] for row in rows.values() if row["code"] != "AUS")
    assert rows["QLD"]["excess"] == max(row["excess"] for row in rows.values() if row["code"] != "AUS")


def test_the_rise_is_six_years_and_the_communities_match():
    payload = facts()
    assert payload["streak"]["rising_full_years"] == [2021, 2022, 2023, 2024, 2025]
    assert payload["jan_sep"] > payload["jan_sep_previous"]
    assert payload["sa4_names_matched"] == payload["sa4_names_known"]
    assert payload["children_0_7"] > 0


def test_summer_is_not_a_higher_daily_rate():
    holiday = facts()["holiday"]
    assert abs(holiday["per_day_holiday"] - holiday["per_day_other"]) < 0.15
    assert holiday["latest_deaths"] > holiday["target"]
