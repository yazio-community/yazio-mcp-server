"""Shaping of the daily summary.

The summary widget is the server's headline tool, and its payload nests further
than it first appears: a meal is `{energy_goal, nutrients}`, not a nutrient map.
Reading it at the wrong level yields a day total of None while still looking
like a successful call, so the nesting is pinned down here.
"""

from __future__ import annotations

from yazio_mcp.tools.diary import _averages, _progress, _shape_meal, _sum_meals

MEALS = {
    "breakfast": {
        "energy_goal": 503.09,
        "nutrients": {
            "energy.energy": 468.99,
            "nutrient.carb": 60.549,
            "nutrient.fat": 21.645,
            "nutrient.protein": 4.78,
        },
    },
    "lunch": {
        "energy_goal": 670.78,
        "nutrients": {
            "energy.energy": 100.0,
            "nutrient.carb": 10.0,
            "nutrient.fat": 1.0,
            "nutrient.protein": 2.0,
        },
    },
}


def test_a_meal_reports_the_nutrients_one_level_down():
    shaped = _shape_meal(MEALS["breakfast"])
    assert shaped["energy_kcal"] == 468.99


def test_a_meal_reports_its_share_of_the_energy_budget():
    assert _shape_meal(MEALS["breakfast"])["energy_goal_kcal"] == 503.09


def test_an_untouched_meal_shapes_without_error():
    assert _shape_meal({"energy_goal": 10.0, "nutrients": {}}) == {"energy_goal_kcal": 10.0}


def test_a_meal_missing_its_nutrients_does_not_crash():
    assert _shape_meal({}) == {}


def test_the_day_total_sums_the_meals():
    """The widget reports no day total, so it is assembled from the slots."""
    total = _sum_meals(MEALS)
    assert total["energy.energy"] == 568.99
    assert total["nutrient.carb"] == 70.549


def test_summing_ignores_the_per_meal_goals():
    """energy_goal sits beside nutrients; adding it in would inflate the total."""
    assert "energy_goal" not in _sum_meals(MEALS)


def test_summing_no_meals_is_empty():
    assert _sum_meals({}) == {}


def test_progress_reports_what_is_left():
    assert _progress(400, 1000) == {
        "consumed": 400,
        "goal": 1000,
        "remaining": 600,
        "percent_of_goal": 40.0,
    }


def test_progress_over_goal_goes_negative():
    assert _progress(1200, 1000)["remaining"] == -200


def test_progress_without_a_goal_omits_the_arithmetic():
    assert _progress(400, None) == {"consumed": 400, "goal": None}


def test_progress_with_a_zero_goal_does_not_divide():
    """A zero goal must not raise; it just has no percentage."""
    assert "percent_of_goal" not in _progress(400, 0)


def test_progress_carries_its_unit():
    assert _progress(500, 2500, "ml")["unit"] == "ml"


def test_averages_ignore_days_without_data():
    entries = [
        {"energy_kcal": 2000, "protein_g": 100},
        {"energy_kcal": 1000, "protein_g": None},
    ]
    averages = _averages(entries)

    assert averages["energy_kcal"] == 1500
    assert averages["protein_g"] == 100
    assert averages["days_counted"] == 2


def test_averages_of_nothing_are_empty():
    assert _averages([]) == {}
