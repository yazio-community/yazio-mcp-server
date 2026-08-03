"""ASGI gate that rejects requests without usable YAZIO credentials.

The tools themselves also resolve credentials (see session.py), so this layer is
not what makes the server secure — it exists so that an unauthenticated client
gets a plain `401` with a `WWW-Authenticate` challenge at the HTTP level, the
way a Basic-auth endpoint is expected to behave, rather than an MCP-level error
buried inside a `200` response body.

Note that Basic auth is not what the MCP specification prescribes for HTTP
transports; it expects OAuth. Clients therefore need to be pointed at this
server with explicit credential headers rather than through an OAuth discovery
flow.
"""

from __future__ import annotations

from starlette.types import ASGIApp, Receive, Scope, Send

from .auth import AuthError, IncompleteCredentials, resolve_credentials

_CHALLENGE = {"WWW-Authenticate": 'Basic realm="YAZIO", charset="UTF-8"'}


class BasicAuthMiddleware:
    """Requires usable credential headers on every request.

    Only the *shape* of the headers is checked here. Whether YAZIO actually
    accepts the credentials is settled by the first token exchange, because
    verifying them up front would add a round trip to requests that may not
    touch the API at all.
    """

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        headers = {
            key.decode("latin-1").lower(): value.decode("latin-1")
            for key, value in scope["headers"]
        }

        try:
            resolve_credentials(headers)
        except IncompleteCredentials as exc:
            await _refuse(send, 400, "Bad Request", str(exc), challenge=False)
            return
        except AuthError as exc:
            await _refuse(send, 401, "Unauthorized", str(exc))
            return

        await self.app(scope, receive, send)


async def _refuse(
    send: Send, status: int, reason: str, detail: str, challenge: bool = True
) -> None:
    body = f"{status} {reason}: {detail}\n".encode()
    headers = [
        (b"content-type", b"text/plain; charset=utf-8"),
        (b"content-length", str(len(body)).encode()),
    ]
    if challenge:
        headers += [(k.encode(), v.encode()) for k, v in _CHALLENGE.items()]

    await send({"type": "http.response.start", "status": status, "headers": headers})
    await send({"type": "http.response.body", "body": body})
