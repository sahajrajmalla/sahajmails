"""The local web application.

``sahajmails run`` starts this. It binds loopback only, guards itself with a
session token and a Host allowlist (see :mod:`~sahajmails.server.security`), and
holds no email logic of its own — every route calls the library.
"""

from __future__ import annotations

from .app import create_app
from .launcher import serve

__all__ = ["create_app", "serve"]
