from road_toll.metrics import mean_year_end_factor, rolling_sum, trajectory_factor


def test_path_is_the_baseline_at_the_end_of_2020_and_half_in_2030():
    assert trajectory_factor(2020, 12) == 1
    assert trajectory_factor(2030, 12) == 0.5


def test_september_2026_is_69_months_along_the_decade():
    assert abs(trajectory_factor(2026, 9) - (1 - 0.5 * 69 / 120)) < 1e-12


def test_factor_does_not_fall_below_half_or_above_the_baseline():
    assert trajectory_factor(2018, 6) == 1
    assert trajectory_factor(2035, 1) == 0.5


def test_model_window_average_sits_between_the_end_years():
    factor = mean_year_end_factor((2022, 2023, 2024, 2025))
    assert trajectory_factor(2025, 12) < factor < trajectory_factor(2022, 12)


def test_rolling_sum_waits_for_a_full_year():
    totals = rolling_sum([1] * 13)
    assert totals[10] is None
    assert totals[11] == 12
    assert totals[12] == 12
