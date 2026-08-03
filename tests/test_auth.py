"""Credential parsing and token caching."""

from __future__ import annotations

import asyncio
import base64

import httpx
import pytest
import respx

from yazio_mcp.auth import (
    AuthError,
    Credentials,
    IncompleteCredentials,
    TokenCache,
    parse_basic_auth,
    resolve_credentials,
)
from yazio_mcp.config import BASE_URL

TOKEN_URL = f"{BASE_URL}/v22/oauth/token"


def basic(username: str, password: str) -> str:
    encoded = base64.b64encode(f"{username}:{password}".encode()).decode()
    return f"Basic {encoded}"


def test_parses_a_well_formed_header():
    credentials = parse_basic_auth(basic("me@example.com", "hunter2"))
    assert credentials == Credentials("me@example.com", "hunter2")


def test_parses_a_password_containing_a_colon():
    # Only the first colon separates the pair, so passwords may contain colons.
    credentials = parse_basic_auth(basic("me", "a:b:c"))
    assert credentials.password == "a:b:c"


def test_accepts_a_lowercase_scheme():
    assert parse_basic_auth(basic("me", "pw").replace("Basic", "basic")).username == "me"


@pytest.mark.parametrize(
    "header",
    [
        None,
        "",
        "Bearer abc123",
        "Basic",
        "Basic !!!not base64!!!",
        f"Basic {base64.b64encode(b'no-separator').decode()}",
    ],
)
def test_rejects_unusable_headers(header):
    with pytest.raises(AuthError):
        parse_basic_auth(header)


def test_resolves_an_authorization_header():
    headers = {"authorization": basic("me@example.com", "hunter2")}
    assert resolve_credentials(headers) == Credentials("me@example.com", "hunter2")


def test_resolves_the_x_auth_pair():
    headers = {"x-auth-username": "me@example.com", "x-auth-password": "hunter2"}
    assert resolve_credentials(headers) == Credentials("me@example.com", "hunter2")


def test_the_x_auth_pair_needs_no_encoding():
    """The point of the pair: values that Basic would have mangled pass through."""
    headers = {"x-auth-username": "me", "x-auth-password": "a:b:c"}
    assert resolve_credentials(headers).password == "a:b:c"


def test_authorization_wins_over_the_x_auth_pair():
    headers = {
        "authorization": basic("header", "pw"),
        "x-auth-username": "ignored",
        "x-auth-password": "ignored",
    }
    assert resolve_credentials(headers) == Credentials("header", "pw")


def test_a_broken_authorization_header_is_not_rescued_by_the_x_auth_pair():
    """Precedence is decided by presence, so a bad Basic header still fails."""
    headers = {
        "authorization": "Basic !!!",
        "x-auth-username": "me",
        "x-auth-password": "pw",
    }
    with pytest.raises(AuthError):
        resolve_credentials(headers)


@pytest.mark.parametrize(
    "headers",
    [
        {"x-auth-username": "me"},
        {"x-auth-password": "pw"},
    ],
)
def test_half_an_x_auth_pair_is_a_bad_request(headers):
    with pytest.raises(IncompleteCredentials):
        resolve_credentials(headers)


def test_an_empty_x_auth_value_still_counts_as_sent():
    """A blank half is a complete pair YAZIO will reject, not a malformed request."""
    headers = {"x-auth-username": "me", "x-auth-password": ""}
    assert resolve_credentials(headers) == Credentials("me", "")


def test_no_credentials_at_all_is_not_a_bad_request():
    """Plain absence must stay a 401, so the client gets a challenge."""
    with pytest.raises(AuthError) as raised:
        resolve_credentials({})

    assert not isinstance(raised.value, IncompleteCredentials)


def test_cache_key_hides_the_password():
    key = Credentials("me", "hunter2").cache_key
    assert "hunter2" not in key
    assert len(key) == 64


def test_cache_key_distinguishes_users():
    assert Credentials("a", "pw").cache_key != Credentials("b", "pw").cache_key


@pytest.mark.asyncio
@respx.mock
async def test_reuses_a_cached_token():
    route = respx.post(TOKEN_URL).mock(
        return_value=httpx.Response(200, json={"access_token": "tok", "expires_in": 3600})
    )
    cache = TokenCache()
    credentials = Credentials("me", "pw")

    assert await cache.token_for(credentials) == "tok"
    assert await cache.token_for(credentials) == "tok"

    assert route.call_count == 1


@pytest.mark.asyncio
@respx.mock
async def test_concurrent_callers_share_one_exchange():
    """A burst of tool calls must not each trigger their own login."""
    route = respx.post(TOKEN_URL).mock(
        return_value=httpx.Response(200, json={"access_token": "tok", "expires_in": 3600})
    )
    cache = TokenCache()
    credentials = Credentials("me", "pw")

    tokens = await asyncio.gather(*(cache.token_for(credentials) for _ in range(10)))

    assert tokens == ["tok"] * 10
    assert route.call_count == 1


@pytest.mark.asyncio
@respx.mock
async def test_different_users_get_different_tokens():
    respx.post(TOKEN_URL).mock(
        side_effect=[
            httpx.Response(200, json={"access_token": "first", "expires_in": 3600}),
            httpx.Response(200, json={"access_token": "second", "expires_in": 3600}),
        ]
    )
    cache = TokenCache()

    assert await cache.token_for(Credentials("a", "pw")) == "first"
    assert await cache.token_for(Credentials("b", "pw")) == "second"


@pytest.mark.asyncio
@respx.mock
async def test_re_authenticates_after_invalidation():
    route = respx.post(TOKEN_URL).mock(
        side_effect=[
            httpx.Response(200, json={"access_token": "old", "expires_in": 3600}),
            httpx.Response(200, json={"access_token": "new", "expires_in": 3600}),
        ]
    )
    cache = TokenCache()
    credentials = Credentials("me", "pw")

    assert await cache.token_for(credentials) == "old"
    cache.invalidate(credentials)
    assert await cache.token_for(credentials) == "new"
    assert route.call_count == 2


@pytest.mark.asyncio
@respx.mock
async def test_a_token_expiring_soon_is_refreshed():
    """Tokens are renewed inside the safety margin, not at the last moment."""
    route = respx.post(TOKEN_URL).mock(
        side_effect=[
            httpx.Response(200, json={"access_token": "old", "expires_in": 30}),
            httpx.Response(200, json={"access_token": "new", "expires_in": 3600}),
        ]
    )
    cache = TokenCache()
    credentials = Credentials("me", "pw")

    assert await cache.token_for(credentials) == "old"
    # 30s of life is inside the 60s margin, so the next call must re-exchange.
    assert await cache.token_for(credentials) == "new"
    assert route.call_count == 2


@pytest.mark.asyncio
@respx.mock
async def test_rejected_credentials_raise():
    respx.post(TOKEN_URL).mock(return_value=httpx.Response(401, json={"error": "invalid_grant"}))

    with pytest.raises(AuthError, match="rejected"):
        await TokenCache().token_for(Credentials("me", "wrong"))


@pytest.mark.asyncio
@respx.mock
async def test_a_response_without_a_token_raises():
    respx.post(TOKEN_URL).mock(return_value=httpx.Response(200, json={"token_type": "bearer"}))

    with pytest.raises(AuthError, match="without an access token"):
        await TokenCache().token_for(Credentials("me", "pw"))
