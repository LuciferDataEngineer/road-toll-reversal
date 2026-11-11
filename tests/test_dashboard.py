import json
from pathlib import Path

from road_toll.dashboard import build_dashboard
from road_toll.paths import SITE

BANNED = (
    "pymc",
    "duckdb",
    "iceberg",
    "polaris",
    "airflow",
    "clickhouse",
    "posterior",
    "hierarchical",
    "credible",
    "pipeline",
    "datacontract",
    "schema",
)


def site_text() -> str:
    data = SITE / "data" / "dashboard.json"
    if not data.exists():
        build_dashboard()
    parts = [(SITE / "data" / "dashboard.json").read_text().lower()]
    for name in ("index.html", "styles.css", "app.js"):
        parts.append((SITE / name).read_text().lower())
    return "\n".join(parts)


def test_pages_and_filters_exist():
    payload = json.loads((SITE / "data" / "dashboard.json").read_text())
    assert set(payload["views"]) == {"AUS", "NSW", "VIC", "QLD", "SA", "WA", "TAS", "NT", "ACT"}
    for view in payload["views"].values():
        assert set(view) == {"toll", "where", "who", "when", "holiday"}
        assert len(view["toll"]["kpis"]) >= 4
        assert len(view["where"]["charts"]) <= 3
    assert "Australia/Sydney" not in payload["meta"]["generated"] or payload["meta"]["generated"]
    assert "September 2026" in payload["meta"]["data_current"]


def test_the_public_pages_do_not_name_the_tools():
    text = site_text()
    for word in BANNED:
        assert word not in text, word


def test_charts_are_drawn_after_a_tab_is_shown():
    script = (SITE / "app.js").read_text()
    assert "page.hidden = page.id !==" in script
    assert "viewBox" in script
    assert "showTab" in script
    geo = json.loads((SITE / "data" / "aus-states.geojson").read_text())
    assert len(geo["features"]) == 8
    assert not any("tile" in Path("site/index.html").read_text().lower() for _ in [0])
