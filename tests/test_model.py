import json

from road_toll.model import clear_hotspots, run_model
from road_toll.paths import MODEL_DIR


def ppc() -> dict:
    path = MODEL_DIR / "ppc.json"
    if not path.exists():
        run_model()
    return json.loads(path.read_text())


def test_predictions_land_on_the_observed_toll():
    summary = ppc()
    assert summary["divergences"] == 0
    assert abs(summary["predicted_total_mean"] - summary["observed_total"]) / summary["observed_total"] < 0.05
    assert summary["predicted_total_p05"] < summary["observed_total"] < summary["predicted_total_p95"]
    assert max(summary["rhat"].values()) < 1.1


def test_regional_communities_clear_their_state_rate():
    rows = clear_hotspots()
    assert len(rows) >= 5
    names = " ".join(row["sa4_name"] for row in rows)
    assert "Wheat Belt" in names or "Wide Bay" in names or "Hume" in names
    assert all(row["rate_low"] > row["state_rate_median"] for row in rows)
    assert all(row["deaths"] >= 5 for row in rows)
