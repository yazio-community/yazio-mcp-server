"""Input rules for create_recipe and set_recipe_photo.

Each of these mirrors something the live API rejects — two of them with a bare
500 rather than a validation error, which is unreadable from the caller's side.
Catching them before the request is what turns them into an explanation.
"""

from __future__ import annotations

import base64

import pytest
from mcp.server.fastmcp.exceptions import ToolError

from yazio_mcp.server import build_server

INGREDIENTS = [
    {"product_id": "a", "amount": 100},
    {"product_id": "b", "amount": 200},
]


async def create(**overrides) -> str:
    """Call create_recipe through the server and return the error it raises.

    The validation under test runs before any network call, so no API mocking is
    needed. A call that passed validation would fail later on the absent
    credentials instead — never silently succeed — so every assertion here is
    still reading the message it means to.
    """
    arguments = {
        "name": "Test",
        "ingredients": INGREDIENTS,
        "portion_count": 2,
        **overrides,
    }

    with pytest.raises(ToolError) as raised:
        await build_server().call_tool("create_recipe", arguments)

    return str(raised.value)


@pytest.mark.asyncio
async def test_a_single_ingredient_is_refused():
    """YAZIO needs two or more; one is rejected outright."""
    message = await create(ingredients=[INGREDIENTS[0]])
    assert "at least two ingredients" in message


@pytest.mark.asyncio
async def test_no_ingredients_is_refused():
    assert "at least two ingredients" in await create(ingredients=[])


@pytest.mark.asyncio
async def test_the_refusal_points_at_the_right_tool():
    """One food is a tracked product, not a recipe — say so."""
    assert "track_product" in await create(ingredients=[INGREDIENTS[0]])


@pytest.mark.asyncio
async def test_a_fractional_portion_count_is_refused():
    """The API answers a bare 500 to this, so it must never reach the network.

    The integer annotation means the schema layer usually catches it first; the
    tool's own check stays as the backstop for a direct call that bypasses
    schema validation.
    """
    message = await create(portion_count=2.5)
    assert "portion_count" in message


@pytest.mark.asyncio
async def test_a_zero_portion_count_is_refused():
    assert "greater than zero" in await create(portion_count=0)


@pytest.mark.asyncio
async def test_a_negative_portion_count_is_refused():
    assert "greater than zero" in await create(portion_count=-2)


@pytest.mark.asyncio
async def test_an_empty_name_is_refused():
    assert "needs a name" in await create(name="   ")


@pytest.mark.asyncio
async def test_portion_count_is_declared_as_an_integer():
    """The schema has to say integer, or a model may send 2.0 and get a 500."""
    tools = await build_server().list_tools()
    schema = next(t for t in tools if t.name == "create_recipe").inputSchema

    assert schema["properties"]["portion_count"]["type"] == "integer"


async def update(**overrides) -> str:
    """Call update_recipe through the server and return the error it raises.

    Like create()'s validation, the checks under test here run before any
    network call, so an argument that passes them fails later on the absent
    credentials instead of network mocking — never silently succeeding.
    """
    arguments = {"recipe_id": "some-id", **overrides}

    with pytest.raises(ToolError) as raised:
        await build_server().call_tool("update_recipe", arguments)

    return str(raised.value)


async def upload_photo(**overrides) -> str:
    """Call set_recipe_photo through the server and return the error it raises.

    Like create()'s validation, the base64 check under test runs before any
    network call, so a well-formed argument fails later on the absent
    credentials instead of network mocking — never silently succeeding.
    """
    arguments = {
        "recipe_id": "some-id",
        "image_base64": base64.b64encode(b"not a real image").decode(),
        **overrides,
    }

    with pytest.raises(ToolError) as raised:
        await build_server().call_tool("set_recipe_photo", arguments)

    return str(raised.value)


@pytest.mark.asyncio
async def test_update_a_single_ingredient_is_refused():
    assert "at least two ingredients" in await update(ingredients=[INGREDIENTS[0]])


@pytest.mark.asyncio
async def test_update_a_fractional_portion_count_is_refused():
    assert "portion_count" in await update(portion_count=2.5)


@pytest.mark.asyncio
async def test_update_a_zero_portion_count_is_refused():
    assert "greater than zero" in await update(portion_count=0)


@pytest.mark.asyncio
async def test_update_a_negative_portion_count_is_refused():
    assert "greater than zero" in await update(portion_count=-2)


@pytest.mark.asyncio
async def test_update_a_blank_name_is_refused():
    assert "needs a name" in await update(name="   ")


@pytest.mark.asyncio
async def test_update_with_no_changes_reaches_the_network():
    """Omitting every optional argument is valid; it should not be refused here."""
    message = await update()
    assert "needs a name" not in message
    assert "at least two ingredients" not in message
    assert "greater than zero" not in message


@pytest.mark.asyncio
async def test_invalid_base64_is_refused():
    message = await upload_photo(image_base64="!!!not base64!!!")
    assert "not valid base64" in message


@pytest.mark.asyncio
async def test_an_empty_image_is_refused():
    message = await upload_photo(image_base64="")
    assert "empty" in message


@pytest.mark.asyncio
async def test_a_well_formed_image_reaches_the_network():
    """A valid image should not be refused by the base64 check itself."""
    message = await upload_photo()
    assert "not valid base64" not in message
    assert "empty" not in message
