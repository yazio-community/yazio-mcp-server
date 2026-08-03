"""An MCP server over the YAZIO API.

Built on the generated `yazio_sdk` package. Clients authenticate to this server with
their YAZIO username and password over HTTP Basic; there is no separate account.
"""

from .server import build_app, build_server

__all__ = ["build_app", "build_server"]
