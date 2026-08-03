"""Reading and creating recipes.

Creating one is the only tool in this server that is more than a translation of
an API call. YAZIO's recipe endpoint does not compute anything: the client is
expected to submit the finished dish's nutrients alongside its ingredients. So
create_recipe resolves every ingredient, scales its nutrients to the amount
used, and sums them before posting.
"""

from __future__ import annotations

import asyncio
import uuid
from typing import Any

from mcp.server.fastmcp import Context, FastMCP
from mcp.server.fastmcp.exceptions import ToolError
from yazio_sdk.api.content import list_featured_recipes as api_list_featured_recipes
from yazio_sdk.api.recipes import create_user_recipe as api_create_user_recipe
from yazio_sdk.api.recipes import delete_user_recipe as api_delete_user_recipe
from yazio_sdk.api.recipes import get_recipe as api_get_recipe
from yazio_sdk.api.recipes import list_favorite_recipes as api_list_favorite_recipes
from yazio_sdk.api.recipes import list_user_recipes as api_list_user_recipes
from yazio_sdk.models import RecipeDraft, RecipeDraftNutrients, RecipeDraftServingsItem

from ..common import plain, round_floats
from ..nutrients import group_nutrients, scale_nutrients, sum_nutrients
from ..session import expect_ok, expect_written, yazio_client
from .products import fetch_product

# How many of the user's own recipes to expand per call. Listing returns bare
# ids, so each one costs a request; this keeps a "show my recipes" call from
# turning into a hundred round trips.
_MAX_EXPANDED = 25


def register(mcp: FastMCP) -> None:
    @mcp.tool()
    async def list_my_recipes(
        ctx: Context, limit: int = _MAX_EXPANDED
    ) -> dict[str, Any]:
        """List the recipes this user has created or saved.

        Args:
            limit: How many recipes to load in full. The total count is always reported.
        """
        async with yazio_client(ctx) as client:
            response = await api_list_user_recipes.asyncio_detailed(client=client)
            recipe_ids = expect_ok(ctx, response, "list your recipes")

            wanted = recipe_ids[: max(0, min(limit, _MAX_EXPANDED))]
            recipes = await asyncio.gather(
                *(_load_recipe(ctx, client, recipe_id) for recipe_id in wanted)
            )

        return round_floats(
            {
                "total": len(recipe_ids),
                "returned": len(recipes),
                "recipes": [
                    _shape_recipe(recipe, brief=True) for recipe in recipes if recipe
                ],
            }
        )

    @mcp.tool()
    async def get_recipe(ctx: Context, recipe_id: str) -> dict[str, Any]:
        """Get one recipe in full: ingredients, instructions and nutrients.

        Nutrients are reported both for the whole dish and per portion.

        Args:
            recipe_id: The recipe's UUID.
        """
        async with yazio_client(ctx) as client:
            recipe = await _load_recipe(ctx, client, recipe_id)

        if recipe is None:
            raise ToolError(f"no recipe found with id {recipe_id}")

        return round_floats(_shape_recipe(recipe, brief=False))

    @mcp.tool()
    async def get_favorite_recipes(ctx: Context) -> dict[str, Any]:
        """List the recipes this user has marked as favourites."""
        async with yazio_client(ctx) as client:
            response = await api_list_favorite_recipes.asyncio_detailed(client=client)
            favorites = expect_ok(ctx, response, "load your favourite recipes")

        return round_floats({"favorites": plain(favorites)})

    @mcp.tool()
    async def browse_recipes(ctx: Context, country_code: str = "de") -> dict[str, Any]:
        """Browse YAZIO's own recipe catalogue for a country.

        This is the editorial collection shown in the app, not the user's own
        recipes. Use list_my_recipes for those.

        Args:
            country_code: Two-letter country code, such as "de" or "us".
        """
        async with yazio_client(ctx) as client:
            response = await api_list_featured_recipes.asyncio_detailed(
                client=client, country_code=country_code.lower()
            )
            catalogue = expect_ok(
                ctx, response, f"browse the {country_code} recipe catalogue"
            )

        return round_floats(
            {"country_code": country_code.lower(), "catalogue": plain(catalogue)}
        )

    @mcp.tool()
    async def create_recipe(
        ctx: Context,
        name: str,
        ingredients: list[dict[str, Any]],
        portion_count: int = 2,
        instructions: list[str] | None = None,
    ) -> dict[str, Any]:
        """Create a recipe from a list of tracked products.

        Each ingredient must name a product from search_products and how much of
        it the recipe uses, either as an `amount` in the product's base unit
        (grams or millilitres) or as a `serving` plus `serving_quantity`:

            [{"product_id": "…", "amount": 250},
             {"product_id": "…", "serving": "can", "serving_quantity": 1}]

        At least two ingredients are required — YAZIO rejects a recipe with one.

        The recipe's nutrients are computed from the ingredients — YAZIO stores
        what the client submits and does not derive them — so every ingredient
        must resolve to a real product before the recipe can be created.

        Args:
            name: Name of the recipe.
            ingredients: Products and quantities, as described above. Two or more.
            portion_count: How many portions the finished recipe makes. Whole
                portions only.
            instructions: Preparation steps, one string per step.
        """
        if not name.strip():
            raise ToolError("a recipe needs a name")
        # YAZIO rejects a one-ingredient recipe outright ("This collection should
        # contain 2 elements or more"). Catching it here explains why, rather
        # than surfacing a 400 from the middle of a submission.
        if len(ingredients) < 2:
            raise ToolError(
                "YAZIO requires at least two ingredients in a recipe; "
                f"got {len(ingredients)}. To log a single food, use track_product."
            )
        # A fractional portion_count is answered with a 500, not a validation
        # error, so it has to be caught here.
        if int(portion_count) != portion_count:
            raise ToolError(
                f"portion_count must be a whole number of portions; got {portion_count}"
            )
        portion_count = int(portion_count)
        if portion_count <= 0:
            raise ToolError("portion_count must be greater than zero")

        async with yazio_client(ctx) as client:
            resolved = await asyncio.gather(
                *(
                    _resolve_ingredient(client, item, index)
                    for index, item in enumerate(ingredients)
                )
            )

            total_nutrients = sum_nutrients([item["nutrients"] for item in resolved])

            draft = RecipeDraft(
                id=str(uuid.uuid4()).upper(),
                name=name.strip(),
                # Must serialise as an integer: YAZIO answers 500 to a
                # fractional portion_count, and 2.0 counts as fractional.
                portion_count=portion_count,
                instructions=list(instructions or []),
                servings=[item["serving_entry"] for item in resolved],
                nutrients=RecipeDraftNutrients.from_dict(total_nutrients),
            )

            response = await api_create_user_recipe.asyncio_detailed(
                client=client, body=draft
            )
            expect_written(ctx, response, f"create the recipe '{name}'")

        per_portion = scale_nutrients(total_nutrients, 1 / float(portion_count))

        return round_floats(
            {
                "created": True,
                "recipe_id": draft.id,
                "name": draft.name,
                "portion_count": portion_count,
                "ingredients": [
                    {
                        "product_id": item["serving_entry"].product_id,
                        "name": item["serving_entry"].name,
                        "amount": item["serving_entry"].amount,
                        "base_unit": item["serving_entry"].base_unit,
                        "serving": plain(item["serving_entry"].serving),
                        "serving_quantity": plain(
                            item["serving_entry"].serving_quantity
                        ),
                    }
                    for item in resolved
                ],
                "nutrients_total": group_nutrients(total_nutrients),
                "nutrients_per_portion": group_nutrients(per_portion),
            }
        )

    @mcp.tool()
    async def delete_recipe(ctx: Context, recipe_id: str) -> dict[str, Any]:
        """Delete one of this user's own recipes.

        Only removes the recipe itself. Diary entries that already logged
        portions of it are untouched — use untrack_item to remove those.

        Args:
            recipe_id: The recipe's UUID, from list_my_recipes.
        """
        async with yazio_client(ctx) as client:
            recipe = await _load_recipe(ctx, client, recipe_id)
            if recipe is None:
                raise ToolError(f"no recipe found with id {recipe_id}")

            response = await api_delete_user_recipe.asyncio_detailed(
                client=client, id=recipe_id
            )
            expect_written(ctx, response, f"delete recipe {recipe_id}")

        return {
            "deleted": True,
            "recipe_id": recipe_id,
            "name": plain(recipe.name),
        }


async def _load_recipe(ctx: Context, client: Any, recipe_id: str) -> Any:
    response = await api_get_recipe.asyncio_detailed(client=client, id=recipe_id)
    if response.status_code == 404:
        return None
    return expect_ok(ctx, response, f"load recipe {recipe_id}")


def _shape_recipe(recipe: Any, brief: bool) -> dict[str, Any]:
    """Present a recipe, optionally without its ingredient list.

    The brief form is used when listing, where a dozen full ingredient lists
    would bury the names the caller is actually choosing between.
    """
    portion_count = plain(recipe.portion_count) or 1
    nutrients = plain(recipe.nutrients) or {}

    shaped: dict[str, Any] = {
        "recipe_id": plain(recipe.id),
        "name": plain(recipe.name),
        "portion_count": portion_count,
        "is_yazio_recipe": plain(recipe.is_yazio_recipe),
        "is_pro_recipe": plain(recipe.is_pro_recipe),
        "nutrients_per_portion": group_nutrients(
            scale_nutrients(nutrients, 1 / portion_count)
        ),
    }

    if not brief:
        shaped["nutrients_total"] = group_nutrients(nutrients)
        shaped["servings"] = plain(recipe.servings)
        shaped["instructions"] = plain(recipe.instructions)
        shaped["locale"] = plain(recipe.locale)
        shaped["image"] = plain(recipe.image)

    return shaped


async def _resolve_ingredient(
    client: Any, item: dict[str, Any], index: int
) -> dict[str, Any]:
    """Turn one ingredient spec into a serving entry plus its scaled nutrients."""
    if not isinstance(item, dict):
        raise ToolError(f"ingredient {index + 1} must be an object with a product_id")

    product_id = item.get("product_id")
    if not product_id:
        raise ToolError(f"ingredient {index + 1} is missing product_id")

    product = await fetch_product(client, str(product_id))
    if product is None:
        raise ToolError(
            f"ingredient {index + 1}: no product found with id {product_id}"
        )

    serving = item.get("serving")
    quantity = item.get("serving_quantity")
    amount = item.get("amount")

    if amount is None and serving is None:
        raise ToolError(
            f"ingredient {index + 1} ({product.get('name')}) needs either an amount "
            "or a serving with serving_quantity"
        )

    # Reuse the tracking rules so a recipe ingredient and a tracked portion mean
    # the same thing for the same numbers.
    from .tracking import _resolve_amount

    resolved_amount = _resolve_amount(product, amount, serving, quantity)

    serving_entry = RecipeDraftServingsItem(
        product_id=str(product_id),
        name=product.get("name") or "",
        producer=product.get("producer") or "",
        base_unit=product.get("base_unit") or "g",
        amount=resolved_amount,
    )
    if serving is not None:
        serving_entry.serving = str(serving)
        serving_entry.serving_quantity = float(quantity if quantity is not None else 1)

    return {
        "serving_entry": serving_entry,
        "nutrients": _nutrients_for_amount(product, resolved_amount),
    }


def _nutrients_for_amount(product: dict[str, Any], amount: float) -> dict[str, float]:
    """Scale a product's nutrient table to the amount a recipe uses.

    YAZIO stores product nutrients per single base unit — per gram, not per
    100 g — so the scale factor is the amount itself. The product payload is
    checked for an explicit basis first in case that ever stops being true.
    """
    nutrients = product.get("nutrients")
    if not isinstance(nutrients, dict) or not nutrients:
        raise ToolError(
            f"product {product.get('name', '')} has no nutrient data, "
            "so it cannot be used as a recipe ingredient"
        )

    basis = product.get("nutrients_base_amount") or product.get("base_amount") or 1.0
    return scale_nutrients(nutrients, amount / float(basis))
