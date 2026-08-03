"""Reshaping and scaling YAZIO's dotted nutrient maps."""

from __future__ import annotations

import pytest

from yazio_mcp.nutrients import group_nutrients, scale_nutrients, sum_nutrients

SAMPLE = {
    "energy.energy": 884.0,
    "nutrient.fat": 100.0,
    "nutrient.carb": 0.0,
    "nutrient.protein": 0.0,
    "mineral.calcium": 0.001,
    "vitamin.e": 0.0142,
    "unknown.thing": 3.0,
}


def test_groups_by_family():
    grouped = group_nutrients(SAMPLE)

    assert grouped["energy_kcal"] == 884.0
    assert grouped["macros"] == {"carb": 0.0, "protein": 0.0, "fat": 100.0}
    assert grouped["minerals"] == pytest.approx({"calcium": 1.0})
    assert grouped["vitamins"] == pytest.approx({"e": 14.2})


def test_micronutrients_are_reported_in_milligrams():
    """In grams they are small enough that rounding would report them as zero."""
    grouped = group_nutrients({"mineral.calcium": 5e-05, "vitamin.c": 0.00012})

    assert grouped["minerals"]["calcium"] == pytest.approx(0.05)
    assert grouped["vitamins"]["c"] == pytest.approx(0.12)


def test_states_its_units():
    assert group_nutrients(SAMPLE)["units"] == {
        "energy": "kcal",
        "macros": "g",
        "minerals": "mg",
        "vitamins": "mg",
        "other": "g",
    }


def test_units_name_only_the_groups_that_are_present():
    assert group_nutrients({"mineral.calcium": 0.001})["units"] == {"minerals": "mg"}


def test_keeps_unrecognised_keys():
    """The spec is inferred from traffic, so unknown families must not vanish."""
    assert group_nutrients(SAMPLE)["other"]["unknown.thing"] == 3.0


def test_macros_lead_in_yazio_order():
    assert list(group_nutrients(SAMPLE)["macros"]) == ["carb", "protein", "fat"]


def test_loses_nothing():
    grouped = group_nutrients(SAMPLE)
    counted = (
        1  # energy
        + len(grouped["macros"])
        + len(grouped["minerals"])
        + len(grouped["vitamins"])
        + len(grouped["other"])
    )
    assert counted == len(SAMPLE)


def test_empty_input_yields_empty_output():
    assert group_nutrients({}) == {}
    assert group_nutrients(None) == {}


def test_omits_groups_that_have_no_values():
    assert "vitamins" not in group_nutrients({"energy.energy": 10.0})


def test_non_numeric_values_go_to_other():
    grouped = group_nutrients({"nutrient.note": "unknown"})
    assert grouped["other"] == {"nutrient.note": "unknown"}


def test_scaling_multiplies_every_value():
    scaled = scale_nutrients({"energy.energy": 9.0, "nutrient.fat": 1.0}, 250)
    assert scaled == {"energy.energy": 2250.0, "nutrient.fat": 250.0}


def test_scaling_keeps_the_dotted_keys():
    """The scaled map is posted back to YAZIO, so its keys must stay verbatim."""
    assert set(scale_nutrients(SAMPLE, 2)) == set(SAMPLE) - {"unknown.thing"} | {"unknown.thing"}


def test_summing_unions_keys():
    total = sum_nutrients(
        [
            {"energy.energy": 100.0, "nutrient.fat": 5.0},
            {"energy.energy": 50.0, "nutrient.protein": 2.0},
        ]
    )
    assert total == {
        "energy.energy": 150.0,
        "nutrient.fat": 5.0,
        "nutrient.protein": 2.0,
    }


def test_summing_nothing_is_empty():
    assert sum_nutrients([]) == {}


def test_a_recipe_round_trip_divides_cleanly():
    """Two ingredients, four portions: per-portion is a quarter of the total."""
    total = sum_nutrients(
        [
            scale_nutrients({"energy.energy": 9.0}, 100),  # 100 g of oil
            scale_nutrients({"energy.energy": 1.0}, 200),  # 200 g of something
        ]
    )
    assert total["energy.energy"] == 1100.0
    assert scale_nutrients(total, 1 / 4)["energy.energy"] == 275.0
