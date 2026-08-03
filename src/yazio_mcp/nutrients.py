"""Reshapes YAZIO's flat, dotted nutrient maps into grouped JSON.

The API returns nutrients as a single flat map with keys like `energy.energy`,
`nutrient.protein`, `mineral.calcium` and `vitamin.b12`. A full product carries
around forty of these. Nothing is dropped here — the grouping only makes the
shape self-describing, so a caller can tell at a glance which numbers are macros
and which are micronutrients, without having to know YAZIO's key vocabulary.

YAZIO stores energy in kilocalories and every other nutrient in grams. Grams are
kept for macros, but minerals and vitamins are converted to milligrams on the way
out: in grams they are values like 0.00012, small enough that any downstream
rounding reports them as zero. Each group's unit is stated in the output so the
mixture cannot be misread.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

# YAZIO's own ordering for the four headline numbers. Anything outside this list
# still comes through, just after these.
_MACRO_ORDER = ("carb", "protein", "fat")

_GROUPS = {
    "nutrient": "macros",
    "mineral": "minerals",
    "vitamin": "vitamins",
}

# What each group is reported in, and the factor from YAZIO's stored grams.
# Milligrams for the micronutrient families keeps them in the range a label
# would print; `other` holds keys we do not recognise, so it is left in the
# unit YAZIO stored.
_UNITS = {
    "macros": ("g", 1.0),
    "minerals": ("mg", 1000.0),
    "vitamins": ("mg", 1000.0),
    "other": ("g", 1.0),
}


def group_nutrients(raw: Mapping[str, Any] | None) -> dict[str, Any]:
    """Turn a flat dotted nutrient map into grouped, unit-annotated JSON.

    Keys that do not follow the `prefix.name` convention, or that use a prefix
    we do not recognise, are preserved verbatim under `other` rather than being
    silently discarded — the spec was inferred from traffic, so new nutrient
    families are expected to turn up.
    """
    if not raw:
        return {}

    grouped: dict[str, dict[str, float]] = {
        "macros": {},
        "minerals": {},
        "vitamins": {},
        "other": {},
    }
    energy_kcal: float | None = None

    for key, value in raw.items():
        if not isinstance(value, (int, float)):
            grouped["other"][key] = value
            continue

        prefix, _, name = key.partition(".")
        if key == "energy.energy":
            energy_kcal = float(value)
        elif name and prefix in _GROUPS:
            grouped[_GROUPS[prefix]][name] = float(value)
        else:
            grouped["other"][key] = float(value)

    units: dict[str, str] = {}
    result: dict[str, Any] = {"units": units}
    if energy_kcal is not None:
        units["energy"] = "kcal"
        result["energy_kcal"] = energy_kcal

    if grouped["macros"]:
        units["macros"] = _UNITS["macros"][0]
        result["macros"] = _ordered(
            _converted(grouped["macros"], "macros"), _MACRO_ORDER
        )
    for group in ("minerals", "vitamins", "other"):
        if grouped[group]:
            units[group] = _UNITS[group][0]
            values = _converted(grouped[group], group)
            result[group] = dict(sorted(values.items()))

    return result


def _converted(values: dict[str, Any], group: str) -> dict[str, Any]:
    """Restate one group's values in that group's reporting unit."""
    factor = _UNITS[group][1]
    if factor == 1.0:
        return values
    return {
        key: value * factor if isinstance(value, float) else value
        for key, value in values.items()
    }


def _ordered(values: dict[str, float], first: tuple[str, ...]) -> dict[str, float]:
    """Sort a group so that well-known keys lead and the rest follow by name."""
    leading = {key: values[key] for key in first if key in values}
    trailing = {
        key: value for key, value in sorted(values.items()) if key not in leading
    }
    return {**leading, **trailing}


def scale_nutrients(raw: Mapping[str, Any] | None, factor: float) -> dict[str, float]:
    """Multiply every numeric nutrient by `factor`, keeping the flat dotted keys.

    Used when building a recipe: YAZIO expects the client to submit the summed
    nutrients of the finished dish, so each ingredient's per-serving values have
    to be scaled to the amount actually used and then added up.
    """
    if not raw:
        return {}

    return {
        key: float(value) * factor
        for key, value in raw.items()
        if isinstance(value, (int, float))
    }


def sum_nutrients(parts: list[Mapping[str, float]]) -> dict[str, float]:
    """Add up several flat nutrient maps, keeping every key that appears in any."""
    total: dict[str, float] = {}
    for part in parts:
        for key, value in part.items():
            total[key] = total.get(key, 0.0) + float(value)
    return total
