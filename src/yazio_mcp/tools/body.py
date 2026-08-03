"""The user's own numbers: profile, weight and logged exercise."""

from __future__ import annotations

import uuid
from typing import Any

from mcp.server.fastmcp import Context, FastMCP
from mcp.server.fastmcp.exceptions import ToolError
from yazio_sdk.api.activity import log_exercise as api_log_exercise
from yazio_sdk.api.body_values import create_body_value as api_create_body_value
from yazio_sdk.api.body_values import get_latest_weight as api_get_latest_weight
from yazio_sdk.api.user import get_user as api_get_user
from yazio_sdk.models import (
    BodyValueEntry,
    BodyValueEntryWeightItem,
    ExerciseEntry,
    ExerciseEntryActivityItem,
)

from ..common import plain, resolve_date, resolve_timestamp, round_floats
from ..session import expect_ok, expect_written, yazio_client


def register(mcp: FastMCP) -> None:
    @mcp.tool()
    async def get_profile(ctx: Context) -> dict[str, Any]:
        """Get the user's profile: goal, activity level, units, height, birth date.

        Useful as context before giving nutrition advice, since it says which
        direction the user is aiming (lose, maintain, gain), how fast, and which
        units they read their numbers in.
        """
        async with yazio_client(ctx) as client:
            response = await api_get_user.asyncio_detailed(client=client)
            profile = expect_ok(ctx, response, "load your profile")

        return round_floats(
            {
                "first_name": plain(profile.first_name),
                "sex": plain(profile.sex),
                "date_of_birth": plain(profile.date_of_birth),
                "body_height_cm": plain(profile.body_height),
                "start_weight_kg": plain(profile.start_weight),
                "goal": plain(profile.goal),
                "weight_change_per_week_kg": plain(profile.weight_change_per_week),
                "activity_degree": plain(profile.activity_degree),
                "diet": plain(profile.diet),
                "country": plain(profile.country),
                "language": plain(profile.language),
                "food_database_country": plain(profile.food_database_country),
                "units": {
                    "mass": plain(profile.unit_mass),
                    "energy": plain(profile.unit_energy),
                    "length": plain(profile.unit_length),
                    "serving": plain(profile.unit_serving),
                    "glucose": plain(profile.unit_glucose),
                },
                "premium_type": plain(profile.premium_type),
                "registration_date": plain(profile.registration_date),
            }
        )

    @mcp.tool()
    async def get_last_weight(ctx: Context, date: str | None = None) -> dict[str, Any]:
        """Get the most recently logged body weight, in kilograms.

        Args:
            date: Look back from this day, as YYYY-MM-DD. Defaults to today.
        """
        # The endpoint rejects a blank `date` even though the spec marks it
        # optional; "last" means the latest entry on or before that day.
        day = resolve_date(date)

        async with yazio_client(ctx) as client:
            response = await api_get_latest_weight.asyncio_detailed(
                client=client, date=day
            )
            entry = expect_ok(ctx, response, "load your last weight entry")

        return round_floats(
            {
                "date": plain(entry.date),
                "weight_kg": plain(entry.value),
                "entry_id": plain(entry.id),
                "source": plain(entry.source),
            }
        )

    @mcp.tool()
    async def log_weight(
        ctx: Context,
        kilograms: float,
        date: str | None = None,
    ) -> dict[str, Any]:
        """Log a body weight measurement.

        Args:
            kilograms: The measured weight in kg.
            date: Day to log against as YYYY-MM-DD. Defaults to today.
        """
        if kilograms <= 0:
            raise ToolError("weight must be greater than zero")

        day = resolve_date(date)
        entry_id = str(uuid.uuid4()).upper()

        async with yazio_client(ctx) as client:
            response = await api_create_body_value.asyncio_detailed(
                client=client,
                body=BodyValueEntry(
                    weight=[
                        BodyValueEntryWeightItem(
                            id=entry_id,
                            value=float(kilograms),
                            date=resolve_timestamp(date),
                        )
                    ]
                ),
            )
            expect_written(ctx, response, f"log a weight of {kilograms} kg")

        return {
            "logged": True,
            "date": day,
            "weight_kg": kilograms,
            "entry_id": entry_id,
        }

    @mcp.tool()
    async def log_exercise(
        ctx: Context,
        energy_kcal: float,
        date: str | None = None,
        steps: float | None = None,
        distance_metres: float | None = None,
    ) -> dict[str, Any]:
        """Log general daily activity: energy burned, and optionally steps and distance.

        This writes the "activity" bucket the app fills from a step counter,
        rather than a named workout.

        Args:
            energy_kcal: Energy burned, in kilocalories.
            date: Day to log against as YYYY-MM-DD. Defaults to today.
            steps: Step count for the day.
            distance_metres: Distance covered, in metres.
        """
        if energy_kcal < 0:
            raise ToolError("energy burned cannot be negative")

        day = resolve_date(date)
        activity = ExerciseEntryActivityItem(
            date=resolve_timestamp(date),
            energy=float(energy_kcal),
            gateway="yazio-mcp",
        )
        if steps is not None:
            activity.steps = float(steps)
        if distance_metres is not None:
            activity.distance = float(distance_metres)

        async with yazio_client(ctx) as client:
            response = await api_log_exercise.asyncio_detailed(
                client=client, body=ExerciseEntry(activity=[activity])
            )
            expect_written(ctx, response, f"log activity for {day}")

        return round_floats(
            {
                "logged": True,
                "date": day,
                "energy_kcal": energy_kcal,
                "steps": steps,
                "distance_metres": distance_metres,
            }
        )
