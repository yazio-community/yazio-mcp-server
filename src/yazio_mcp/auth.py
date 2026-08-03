"""Turns the credentials a client sends into a YAZIO bearer token.

There is no separate account for this server: the username and password used to
authenticate to the MCP endpoint *are* the YAZIO login. A request carries them
either in an `Authorization: Basic` header or, for clients that cannot assemble
the base64 credential themselves, in a plain `X-Auth-Username`/`X-Auth-Password`
pair. This module turns either form into the token the API expects.

Exchanging on every tool call would mean a second round trip per call, so tokens
are cached per credential pair. YAZIO does hand out a refresh token, but the
password grant is just as cheap and we hold the password for the lifetime of the
connection anyway, so expiry simply re-runs the original exchange.
"""

from __future__ import annotations

import asyncio
import base64
import binascii
import hashlib
import time
from collections.abc import Mapping
from dataclasses import dataclass

from yazio_sdk import Client
from yazio_sdk.api.authentication import create_token
from yazio_sdk.models import OAuthTokenRequest

from .config import BASE_URL, CLIENT_ID, CLIENT_SECRET, DEFAULT_HEADERS, HTTP_TIMEOUT

# Re-authenticate this many seconds before the token actually expires, so a call
# that is already in flight cannot have its token expire underneath it.
_EXPIRY_MARGIN = 60.0

# The header pair that carries the login unencoded, for clients that can only
# pass values through verbatim and so cannot build a Basic credential.
USERNAME_HEADER = "X-Auth-Username"
PASSWORD_HEADER = "X-Auth-Password"


class AuthError(Exception):
    """The credentials are missing, malformed, or rejected by YAZIO."""


class IncompleteCredentials(AuthError):
    """Half of an `X-Auth-*` pair arrived, which is a mistake rather than an
    absence of credentials.

    Kept apart from the other failures so the HTTP layer can answer 400 instead
    of 401: challenging a client that clearly *tried* to authenticate would only
    have it resend the same broken pair.
    """


@dataclass(frozen=True)
class Credentials:
    """A YAZIO login, as sent by the client."""

    username: str
    password: str

    @property
    def cache_key(self) -> str:
        """A stable, non-reversible identifier for this pair.

        Used as the token-cache key so that the cache never holds a plaintext
        password in a dict key that might end up in a traceback or log line.
        """
        digest = hashlib.sha256(f"{self.username}:{self.password}".encode())
        return digest.hexdigest()


def parse_basic_auth(header: str | None) -> Credentials:
    """Decode an `Authorization: Basic` header into credentials.

    Raises:
        AuthError: if the header is absent or not a well-formed Basic header.
    """
    if not header:
        raise AuthError("missing Authorization header")

    scheme, _, encoded = header.partition(" ")
    if scheme.lower() != "basic" or not encoded:
        raise AuthError("expected an Authorization: Basic header")

    try:
        decoded = base64.b64decode(encoded, validate=True).decode("utf-8")
    except (binascii.Error, UnicodeDecodeError) as exc:
        raise AuthError("Authorization header is not valid base64") from exc

    username, separator, password = decoded.partition(":")
    if not separator:
        raise AuthError("Basic credentials must be in username:password form")

    return Credentials(username=username, password=password)


def resolve_credentials(headers: Mapping[str, str | None]) -> Credentials:
    """Read a login out of a request's headers.

    `Authorization` wins whenever it is present: a client that sends both forms
    has one of them configured by mistake, and silently preferring the other
    would make the mistake invisible.

    Args:
        headers: The request headers, looked up in lower case. Any mapping whose
            `get` is case-insensitive (such as Starlette's) works too.

    An empty header value is not a credential, so once either `X-Auth-*` header
    is on the request both of them have to carry something. A client that wired
    up the pair and left a field blank is told which half it still owes, rather
    than having the blank forwarded to YAZIO as if it were a real credential.

    Raises:
        IncompleteCredentials: if an `X-Auth-*` header is present but the pair
            does not amount to a username *and* a password.
        AuthError: if no credentials are present, or the `Authorization` header
            is not a well-formed Basic header.
    """
    authorization = headers.get("authorization")
    if authorization:
        return parse_basic_auth(authorization)

    username = headers.get(USERNAME_HEADER.lower())
    password = headers.get(PASSWORD_HEADER.lower())

    if username and password:
        return Credentials(username=username, password=password)

    if username is not None or password is not None:
        blank = [
            header
            for header, value in ((USERNAME_HEADER, username), (PASSWORD_HEADER, password))
            if not value
        ]
        raise IncompleteCredentials(
            f"{USERNAME_HEADER} and {PASSWORD_HEADER} must both carry a value; "
            f"missing or empty: {', '.join(blank)}"
        )

    raise AuthError(
        f"missing credentials: send an Authorization: Basic header, or "
        f"{USERNAME_HEADER} and {PASSWORD_HEADER}"
    )


@dataclass
class _CachedToken:
    access_token: str
    expires_at: float

    @property
    def is_fresh(self) -> bool:
        return time.monotonic() < self.expires_at - _EXPIRY_MARGIN


class TokenCache:
    """Caches YAZIO access tokens, one per credential pair.

    A per-key lock keeps a burst of concurrent tool calls from firing off a
    stampede of identical token exchanges: the first caller authenticates while
    the rest wait, then all of them see the cached token.
    """

    def __init__(self) -> None:
        self._tokens: dict[str, _CachedToken] = {}
        self._locks: dict[str, asyncio.Lock] = {}

    async def token_for(self, credentials: Credentials) -> str:
        key = credentials.cache_key

        cached = self._tokens.get(key)
        if cached is not None and cached.is_fresh:
            return cached.access_token

        lock = self._locks.setdefault(key, asyncio.Lock())
        async with lock:
            # Another caller may have refreshed while we waited for the lock.
            cached = self._tokens.get(key)
            if cached is not None and cached.is_fresh:
                return cached.access_token

            token = await self._authenticate(credentials)
            self._tokens[key] = token
            return token.access_token

    def invalidate(self, credentials: Credentials) -> None:
        """Drop a cached token.

        Called when the API rejects a token we believed was still valid, so the
        next call re-authenticates instead of replaying the dead token.
        """
        self._tokens.pop(credentials.cache_key, None)

    async def _authenticate(self, credentials: Credentials) -> _CachedToken:
        client = Client(
            base_url=BASE_URL, timeout=HTTP_TIMEOUT, headers=DEFAULT_HEADERS
        )
        request = OAuthTokenRequest(
            username=credentials.username,
            password=credentials.password,
            client_id=CLIENT_ID,
            client_secret=CLIENT_SECRET,
            grant_type="password",
        )

        async with client as client:
            response = await create_token.asyncio_detailed(client=client, body=request)

        if response.status_code != 200 or response.parsed is None:
            raise AuthError("YAZIO rejected these credentials")

        access_token = response.parsed.access_token
        if not isinstance(access_token, str) or not access_token:
            raise AuthError("YAZIO returned a token response without an access token")

        # `expires_in` is optional in the spec; fall back to an hour, which is
        # what the live API has been observed to return.
        expires_in = response.parsed.expires_in
        lifetime = float(expires_in) if isinstance(expires_in, (int, float)) else 3600.0

        return _CachedToken(
            access_token=access_token,
            expires_at=time.monotonic() + lifetime,
        )
