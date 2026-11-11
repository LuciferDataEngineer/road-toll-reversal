"""Repository paths shared by the pipeline, model, and dashboard."""

from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SNAPSHOT = ROOT / "data" / "snapshot"
MANIFEST = ROOT / "data" / "manifest.json"
GOLD_DIR = ROOT / "data" / "gold"
MODEL_DIR = ROOT / "data" / "model"
BENCHMARK_DIR = ROOT / "data" / "benchmark"
DB_PATH = GOLD_DIR / "road_toll.duckdb"
FACTS_PATH = GOLD_DIR / "facts.json"
HOTSPOTS_PATH = GOLD_DIR / "hotspots.json"
SITE = ROOT / "site"
CONTRACTS = ROOT / "contracts"

LATEST_YEAR = 2026
LATEST_MONTH = 9
BASELINE_YEARS = (2018, 2019, 2020)
MODEL_YEARS = (2022, 2023, 2024, 2025)
HORIZON_YEAR = 2030
DEATHS_REDUCTION = 0.5

STATES = ("NSW", "VIC", "QLD", "SA", "WA", "TAS", "NT", "ACT")
STATE_NAMES = {
    "AUS": "Australia",
    "NSW": "New South Wales",
    "VIC": "Victoria",
    "QLD": "Queensland",
    "SA": "South Australia",
    "WA": "Western Australia",
    "TAS": "Tasmania",
    "NT": "Northern Territory",
    "ACT": "Australian Capital Territory",
}
ABS_REGION = {
    "AUS": "AUS",
    "NSW": "1",
    "VIC": "2",
    "QLD": "3",
    "SA": "4",
    "WA": "5",
    "TAS": "6",
    "NT": "7",
    "ACT": "8",
}
STE_FROM_DIGIT = {code: state for state, code in ABS_REGION.items() if state != "AUS"}

ROAD_USERS = ("Driver", "Passenger", "Pedestrian", "Motorcyclist", "Cyclist")
VULNERABLE = ("Pedestrian", "Motorcyclist", "Cyclist")
AGE_BANDS = ("0–7", "8–16", "17–25", "26–39", "40–64", "65–74", "75+")
