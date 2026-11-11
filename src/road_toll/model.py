"""Hierarchical death rates for communities and road users.

Each SA4 is pulled toward its own state, and road-user groups pool strength
across the country. The result is a likely range for the annual death rate,
so a small community is not ranked on one or two deaths alone.
"""

from __future__ import annotations

import json

import duckdb
import numpy as np

from road_toll.paths import DB_PATH, GOLD_DIR, HOTSPOTS_PATH, MODEL_DIR, ROAD_USERS

DRAWS = 500
TUNE = 500
CHAINS = 4
SEED = 20261111
TARGET_ACCEPT = 0.95


def _load_frame():
    con = duckdb.connect(str(DB_PATH), read_only=True)
    frame = con.execute(
        """
        SELECT sa4_code, sa4_name, state, road_user_group, year, deaths, population
        FROM sa4_group_year
        WHERE population >= 100 AND state IS NOT NULL
        ORDER BY state, sa4_name, road_user_group, year
        """
    ).fetchdf()
    frame = frame[frame["road_user_group"].isin(ROAD_USERS)].copy()
    targets = con.execute(
        "SELECT state, road_user_group, target_rate FROM group_target_rate"
    ).fetchdf()
    con.close()
    return frame, targets


def run_model(draws: int = DRAWS, tune: int = TUNE, chains: int = CHAINS) -> dict:
    import pymc as pm

    frame, targets = _load_frame()
    if frame.empty:
        raise RuntimeError("sa4_group_year is empty. Run the pipeline first.")

    sa4_codes = frame[["sa4_code", "sa4_name", "state"]].drop_duplicates().sort_values("sa4_code")
    sa4_list = sa4_codes["sa4_code"].tolist()
    states = sorted(sa4_codes["state"].unique())
    groups = [group for group in ROAD_USERS if group in set(frame["road_user_group"])]
    sa4_index = {code: index for index, code in enumerate(sa4_list)}
    state_index = {code: index for index, code in enumerate(states)}
    group_index = {code: index for index, code in enumerate(groups)}
    sa4_state = sa4_codes["state"].map(state_index).to_numpy(dtype=int)

    obs_sa4 = frame["sa4_code"].map(sa4_index).to_numpy()
    obs_group = frame["road_user_group"].map(group_index).to_numpy()
    deaths = frame["deaths"].to_numpy()
    exposure = frame["population"].to_numpy() / 100_000.0

    coords = {"sa4": sa4_list, "state": states, "group": groups}
    # One seed per chain, spawned from a single SeedSequence, and chains run
    # one at a time so the schedule does not depend on how many cores the runner has.
    chain_seeds = [
        int(seed) for seed in np.random.SeedSequence(SEED).generate_state(chains)
    ]
    with pm.Model(coords=coords):
        # Non-centred: the raw effects are unit normal and the scale is separate,
        # which keeps the sampler off the funnel between a variance and its offsets.
        sigma_state = pm.HalfNormal("sigma_state", 0.5)
        sigma_sa4 = pm.HalfNormal("sigma_sa4", 0.5)
        sigma_group = pm.HalfNormal("sigma_group", 0.5)
        mu = pm.Normal("mu", mu=np.log(4.5), sigma=1.0)
        state_raw = pm.Normal("state_raw", 0.0, 1.0, dims="state")
        sa4_raw = pm.Normal("sa4_raw", 0.0, 1.0, dims="sa4")
        group_raw = pm.Normal("group_raw", 0.0, 1.0, dims="group")
        u_state = pm.Deterministic("u_state", state_raw * sigma_state, dims="state")
        u_sa4 = pm.Deterministic(
            "u_sa4",
            u_state[sa4_state] + sa4_raw * sigma_sa4,
            dims="sa4",
        )
        v_group = pm.Deterministic("v_group", group_raw * sigma_group, dims="group")
        log_rate = mu + u_sa4[obs_sa4] + v_group[obs_group]
        pm.Poisson("y", mu=exposure * pm.math.exp(log_rate), observed=deaths)

        idata = pm.sample(
            draws=draws,
            tune=tune,
            chains=chains,
            cores=1,
            random_seed=chain_seeds,
            progressbar=False,
            target_accept=TARGET_ACCEPT,
            compute_convergence_checks=True,
        )
        ppc = pm.sample_posterior_predictive(
            idata,
            random_seed=SEED + 10_000,
            progressbar=False,
        )

    def _samples(group, name: str) -> np.ndarray:
        data = group[name]
        if hasattr(data, "stack"):
            data = data.stack(sample=("chain", "draw"))
        values = np.asarray(data.values if hasattr(data, "values") else data)
        # sample axis is last after stack
        return np.moveaxis(values, -1, 0)

    mu_s = _samples(idata.posterior, "mu")
    u_state_s = _samples(idata.posterior, "u_state")
    u_sa4_s = _samples(idata.posterior, "u_sa4")
    v_group_s = _samples(idata.posterior, "v_group")

    target_lookup = {
        (row.state, row.road_user_group): float(row.target_rate)
        for row in targets.itertuples(index=False)
    }
    latest_pop = (
        frame.sort_values("year")
        .groupby(["sa4_code", "road_user_group"], as_index=False)
        .tail(1)
        .set_index(["sa4_code", "road_user_group"])["population"]
    )
    death_totals = frame.groupby(["sa4_code", "road_user_group"])["deaths"].sum()
    name_lookup = dict(zip(sa4_codes["sa4_code"], sa4_codes["sa4_name"], strict=True))
    state_lookup = dict(zip(sa4_codes["sa4_code"], sa4_codes["state"], strict=True))

    records = []
    for sa4_pos, sa4 in enumerate(sa4_list):
        state = state_lookup[sa4]
        state_pos = state_index[state]
        for group_pos, group in enumerate(groups):
            log_rate = mu_s + u_sa4_s[:, sa4_pos] + v_group_s[:, group_pos]
            log_state = mu_s + u_state_s[:, state_pos] + v_group_s[:, group_pos]
            rate = np.exp(log_rate)
            state_rate = np.exp(log_state)
            observed = int(death_totals.get((sa4, group), 0))
            population = int(latest_pop.get((sa4, group), 0))
            gap = rate - state_rate
            excess = gap * population / 100_000.0
            target_rate = target_lookup.get((state, group))
            above_path = float(np.mean(rate > target_rate)) if target_rate is not None else None
            records.append(
                {
                    "sa4_code": sa4,
                    "sa4_name": name_lookup[sa4],
                    "state": state,
                    "road_user_group": group,
                    "deaths": observed,
                    "population": population,
                    "rate_low": float(np.quantile(rate, 0.1)),
                    "rate_median": float(np.median(rate)),
                    "rate_high": float(np.quantile(rate, 0.9)),
                    "state_rate_median": float(np.median(state_rate)),
                    "p_above_state": float(np.mean(rate > state_rate)),
                    "excess_per_year": float(np.median(excess)),
                    "target_rate": target_rate,
                    "p_above_path": above_path,
                }
            )

    y_rep = _samples(ppc.posterior_predictive, "y")
    predicted_totals = y_rep.sum(axis=-1).reshape(-1)
    observed_total = int(deaths.sum())
    zero_obs = float(np.mean(deaths == 0))
    zero_pred = float(np.mean(y_rep == 0))
    import arviz as az

    summary = az.summary(idata, var_names=["mu", "sigma_state", "sigma_sa4", "sigma_group"])
    rhat = {name: float(summary.loc[name, "r_hat"]) for name in summary.index}
    diverging = idata.sample_stats["diverging"]
    divergences = int(np.asarray(getattr(diverging, "values", diverging)).sum())
    ppc_summary = {
        "observed_total": observed_total,
        "predicted_total_mean": float(predicted_totals.mean()),
        "predicted_total_p05": float(np.quantile(predicted_totals, 0.05)),
        "predicted_total_p95": float(np.quantile(predicted_totals, 0.95)),
        "zero_share_observed": zero_obs,
        "zero_share_predicted": zero_pred,
        "rhat": rhat,
        "divergences": divergences,
        "random_seed": SEED,
        "chain_seeds": chain_seeds,
        "target_accept": TARGET_ACCEPT,
        "draws": draws,
        "tune": tune,
        "chains": chains,
        "years": "2022-2025",
        "areas": len(sa4_list),
        "groups": groups,
    }
    MODEL_DIR.mkdir(parents=True, exist_ok=True)
    (MODEL_DIR / "ppc.json").write_text(json.dumps(ppc_summary, indent=2) + "\n")

    hotspots = [
        row
        for row in records
        if row["deaths"] >= 5 and row["p_above_state"] >= 0.9 and row["excess_per_year"] > 0
    ]
    hotspots.sort(key=lambda row: (-row["p_above_state"], -row["excess_per_year"]))
    GOLD_DIR.mkdir(parents=True, exist_ok=True)
    HOTSPOTS_PATH.write_text(json.dumps(hotspots, indent=2) + "\n")

    import pandas as pd

    table = pd.DataFrame(records)
    con = duckdb.connect(str(DB_PATH))
    con.register("sa4_risk_frame", table)
    con.execute("CREATE OR REPLACE TABLE sa4_risk AS SELECT * FROM sa4_risk_frame")
    con.close()

    kept = [row for row in hotspots if row["rate_low"] > row["state_rate_median"]]
    print(
        f"Model fit {len(sa4_list)} communities x {len(groups)} road users. "
        f"Predicted deaths {ppc_summary['predicted_total_mean']:.0f} "
        f"against {observed_total} observed. "
        f"{len(kept)} places sit clearly above their state."
    )
    return {"ppc": ppc_summary, "hotspots": kept, "all_flagged": hotspots}


def clear_hotspots() -> list[dict]:
    """Places whose likely range sits entirely above their state rate."""
    rows = json.loads(HOTSPOTS_PATH.read_text())
    return [row for row in rows if row["rate_low"] > row["state_rate_median"]]
