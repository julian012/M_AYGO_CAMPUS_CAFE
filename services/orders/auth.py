"""Trusted principal extraction for the Orders service.

The principal is the pair (iss, sub) taken ONLY from the claims that API Gateway's
JWT authorizer verified and placed in the HTTP API v2 event:
    event["requestContext"]["authorizer"]["jwt"]["claims"]
Headers, body, email or any client-supplied field are never consulted. If the
context is absent or incomplete the request fails closed.
"""
import os
from collections import namedtuple

from common import ApiError

Principal = namedtuple("Principal", ["iss", "sub"])


def principal_from_event(event):
    claims = (
        ((event.get("requestContext") or {}).get("authorizer") or {}).get("jwt") or {}
    ).get("claims")
    iss = claims.get("iss") if isinstance(claims, dict) else None
    sub = claims.get("sub") if isinstance(claims, dict) else None
    if not isinstance(iss, str) or not iss.strip() or not isinstance(sub, str) or not sub.strip():
        raise ApiError(401, "UNAUTHORIZED", "Authentication required.")
    expected = os.environ.get("EXPECTED_ISSUER", "")
    if expected and iss != expected:
        # Defense in depth: the gateway already pins the issuer.
        raise ApiError(401, "UNAUTHORIZED", "Authentication required.")
    return Principal(iss, sub)
