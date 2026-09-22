"""Launch-time configuration for the Meltano UI server."""

from __future__ import annotations

import secrets
import typing as t
from dataclasses import dataclass, field

#: Default port. Deliberately not 5000: that was the old Meltano UI's port and
#: it collides with the macOS AirPlay Receiver.
DEFAULT_PORT = 5001
DEFAULT_HOST = "127.0.0.1"

#: Hostnames that may appear in the `Host` header. Anything else is rejected to
#: defeat DNS rebinding - see `meltano.ui.security`.
LOOPBACK_HOSTS = frozenset({"127.0.0.1", "localhost", "::1", "[::1]"})


def _generate_token() -> str:
    return secrets.token_urlsafe(32)


@dataclass(frozen=True, kw_only=True, slots=True)
class UIServerSettings:
    """Settings for a single run of the UI server.

    The auth token is regenerated on every launch and is never written to disk.
    """

    host: str = DEFAULT_HOST
    port: int = DEFAULT_PORT
    open_browser: bool = True
    readonly: bool = False
    reload: bool = False
    token: str = field(default_factory=_generate_token, repr=False)

    #: Additional acceptable `Host` header values. The ASGI test client sends
    #: `Host: testserver`, so tests widen the allowlist here rather than
    #: stubbing out `require_auth` and losing coverage of the real path.
    extra_allowed_hosts: frozenset[str] = frozenset()

    @property
    def is_loopback(self) -> bool:
        """Whether the server is bound to a loopback address.

        Returns:
            True if the bind address is loopback-only.
        """
        return self.host in LOOPBACK_HOSTS

    @property
    def allowed_hosts(self) -> frozenset[str]:
        """Host header values this server will accept.

        Returns:
            The permitted ``Host`` header values, including the port.
        """
        hosts = LOOPBACK_HOSTS if self.is_loopback else {self.host}
        allowed: set[str] = set()
        for host in hosts:
            needs_brackets = ":" in host and not host.startswith("[")
            bracketed = f"[{host}]" if needs_brackets else host
            allowed.add(bracketed)
            allowed.add(f"{bracketed}:{self.port}")
        return frozenset(allowed | set(self.extra_allowed_hosts))

    @property
    def url(self) -> str:
        """The browsable URL for this server, including the auth token.

        Returns:
            A URL that authenticates the first request.
        """
        host = f"[{self.host}]" if ":" in self.host else self.host
        return f"http://{host}:{self.port}/?token={self.token}"

    @property
    def origin(self) -> str:
        """The server's own origin, used to validate the ``Origin`` header.

        Returns:
            The scheme://host:port origin string.
        """
        host = f"[{self.host}]" if ":" in self.host else self.host
        return f"http://{host}:{self.port}"

    def to_meta(self) -> dict[str, t.Any]:
        """Return the non-secret subset of these settings.

        Returns:
            A JSON-serializable mapping. Never includes the token.
        """
        return {
            "host": self.host,
            "port": self.port,
            "readonly": self.readonly,
        }
