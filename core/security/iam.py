"""
core/security/iam.py — Identity & Access Management (RBAC + JWT)
=================================================================
Provides:
  - Role enum with a clear privilege hierarchy (ADMIN > OPERATOR > VIEWER)
  - JWTManager: issue and verify signed tokens (HS256, configurable expiry)
  - require_role(): FastAPI Depends factory for endpoint protection
  - iam_guard(): Python decorator for protecting non-HTTP functions

DEPENDENCY: PyJWT >= 2.0  (pip install PyJWT)
            The vault secret "JWT_SECRET_KEY" must be set.
            If absent, a random key is generated at startup (tokens survive
            only for the lifetime of the process — use a real secret in prod).

Usage — FastAPI endpoint:
    from core.security.iam import require_role

    @app.post("/api/v1/execute")
    async def execute(req: ExecuteRequest, user=Depends(require_role("operator"))):
        ...

Usage — standalone function:
    from core.security.iam import iam_guard

    @iam_guard("admin")
    def dangerous_operation(token: str, ...):
        ...  # token is validated before body runs
"""

import logging
import os
import secrets
import time
from dataclasses import dataclass
from enum import Enum
from functools import wraps
from typing import Optional

logger = logging.getLogger(__name__)

# ── JWT import with clear install guidance ────────────────────────────────────
try:
    import jwt as _jwt  # PyJWT
    _JWT_AVAILABLE = True
except ImportError:
    _jwt = None  # type: ignore
    _JWT_AVAILABLE = False
    logger.warning(
        "[IAM] PyJWT not installed. JWT features disabled. Run: pip install PyJWT"
    )


# ── Role Hierarchy ────────────────────────────────────────────────────────────

class Role(str, Enum):
    ADMIN    = "admin"     # Full system control
    OPERATOR = "operator"  # Can execute skills, manage state
    VIEWER   = "viewer"    # Read-only access

# Higher index = more privilege
_ROLE_RANK: dict[Role, int] = {
    Role.VIEWER:   0,
    Role.OPERATOR: 1,
    Role.ADMIN:    2,
}

def role_satisfies(user_role: Role, required_role: Role) -> bool:
    """Returns True if user_role meets or exceeds required_role."""
    return _ROLE_RANK.get(user_role, -1) >= _ROLE_RANK.get(required_role, 99)


# ── User Principal ────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class UserPrincipal:
    user_id: str
    role: Role
    issued_at: float
    expires_at: float

    @property
    def is_expired(self) -> bool:
        return time.time() > self.expires_at


# ── JWT Manager ───────────────────────────────────────────────────────────────

class JWTManager:
    """
    Issues and validates HS256-signed JWT tokens.

    Token payload:
        sub   — user_id
        role  — Role value ("admin" | "operator" | "viewer")
        iat   — issued-at  (Unix timestamp)
        exp   — expires-at (Unix timestamp)
    """

    ALGORITHM = "HS256"

    def __init__(self, secret_key: Optional[str] = None, default_ttl_seconds: int = 3600) -> None:
        if secret_key:
            self._key = secret_key
        else:
            # Fetch from vault or fall back to a process-scoped random key
            from core.security.vault import get_secrets_manager
            vault = get_secrets_manager()
            stored = vault.get_secret("JWT_SECRET_KEY")
            if stored:
                self._key = stored
            else:
                self._key = secrets.token_hex(32)
                logger.warning(
                    "[IAM] JWT_SECRET_KEY not set. Using ephemeral key — "
                    "tokens will not survive process restart. Set JWT_SECRET_KEY in .env."
                )

        self._ttl = default_ttl_seconds

    def create_token(
        self,
        user_id: str,
        role: Role,
        expires_in: Optional[int] = None,
    ) -> str:
        """
        Issue a signed JWT for the given user_id and role.

        Args:
            user_id:    Stable identifier (Telegram chat_id, API key prefix, etc.)
            role:       The Role granted to this token.
            expires_in: TTL in seconds (defaults to self._ttl).

        Returns:
            Signed JWT string.

        Raises:
            RuntimeError: If PyJWT is not installed.
        """
        if not _JWT_AVAILABLE:
            raise RuntimeError("PyJWT not installed. Run: pip install PyJWT")

        now = int(time.time())
        ttl = expires_in if expires_in is not None else self._ttl
        payload = {
            "sub":  user_id,
            "role": role.value,
            "iat":  now,
            "exp":  now + ttl,
        }
        token: str = _jwt.encode(payload, self._key, algorithm=self.ALGORITHM)
        logger.debug("[IAM] Token issued: sub=%s role=%s ttl=%ds", user_id, role.value, ttl)
        return token

    def verify_token(self, token: str) -> UserPrincipal:
        """
        Validate and decode a JWT.

        Returns:
            UserPrincipal on success.

        Raises:
            ValueError:  Token is malformed, expired, or has an invalid signature.
            RuntimeError: PyJWT not installed.
        """
        if not _JWT_AVAILABLE:
            raise RuntimeError("PyJWT not installed. Run: pip install PyJWT")

        try:
            payload: dict = _jwt.decode(
                token,
                self._key,
                algorithms=[self.ALGORITHM],
                options={"require": ["sub", "role", "iat", "exp"]},
            )
        except _jwt.ExpiredSignatureError:
            raise ValueError("[IAM] Token has expired.")
        except _jwt.InvalidTokenError as exc:
            raise ValueError(f"[IAM] Invalid token: {exc}")

        try:
            role = Role(payload["role"])
        except ValueError:
            raise ValueError(f"[IAM] Unknown role in token: {payload['role']!r}")

        return UserPrincipal(
            user_id=payload["sub"],
            role=role,
            issued_at=float(payload["iat"]),
            expires_at=float(payload["exp"]),
        )


# ── Module-level JWTManager singleton ────────────────────────────────────────

_jwt_manager: Optional[JWTManager] = None

def get_jwt_manager() -> JWTManager:
    global _jwt_manager
    if _jwt_manager is None:
        _jwt_manager = JWTManager()
    return _jwt_manager


# ── FastAPI Integration: require_role() ──────────────────────────────────────

def require_role(minimum_role: str):
    """
    FastAPI Depends factory. Validates the Authorization: Bearer <token> header
    and enforces that the caller's role meets or exceeds `minimum_role`.

    Usage:
        from fastapi import Depends
        from core.security.iam import require_role

        @app.post("/api/v1/execute")
        async def execute(req: Body, user=Depends(require_role("operator"))):
            # user is a UserPrincipal if we reach here
            ...

    Raises HTTP 401 if token is missing/invalid, 403 if role is insufficient.
    """
    # Import here to avoid hard dependency for users who don't use FastAPI
    from fastapi import Depends, HTTPException, status
    from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

    _bearer = HTTPBearer(auto_error=False)
    _required = Role(minimum_role)

    async def _dependency(
        credentials: Optional[HTTPAuthorizationCredentials] = Depends(_bearer),
    ) -> UserPrincipal:
        if credentials is None:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Missing Authorization header (Bearer token required).",
                headers={"WWW-Authenticate": "Bearer"},
            )

        try:
            principal = get_jwt_manager().verify_token(credentials.credentials)
        except ValueError as exc:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail=str(exc),
                headers={"WWW-Authenticate": "Bearer"},
            )

        if not role_satisfies(principal.role, _required):
            logger.warning(
                "[IAM] Access denied: user=%s role=%s required=%s",
                principal.user_id, principal.role.value, minimum_role,
            )
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"Role '{principal.role.value}' does not meet required '{minimum_role}'.",
            )

        logger.debug(
            "[IAM] Access granted: user=%s role=%s endpoint_requires=%s",
            principal.user_id, principal.role.value, minimum_role,
        )
        return principal

    return _dependency


# ── Python Decorator: iam_guard() ─────────────────────────────────────────────

def iam_guard(minimum_role: str):
    """
    Decorator for non-HTTP (CLI, internal) functions.

    The decorated function MUST accept `token: str` as its first argument.
    The decorator validates the token before executing the function body.

    Usage:
        @iam_guard("admin")
        def shutdown_system(token: str, reason: str):
            ...

        shutdown_system(token=my_jwt, reason="maintenance")

    Raises:
        ValueError: Token invalid or insufficient role.
    """
    _required = Role(minimum_role)

    def decorator(fn):
        @wraps(fn)
        def wrapper(*args, **kwargs):
            # Accept token as first positional arg or as kwarg
            token: Optional[str] = kwargs.get("token")
            if token is None and args:
                token = args[0]
                args = args[1:]

            if not token:
                raise ValueError(f"[IAM] @iam_guard('{minimum_role}'): 'token' argument is required.")

            try:
                principal = get_jwt_manager().verify_token(token)
            except ValueError as exc:
                raise ValueError(f"[IAM] Token validation failed: {exc}") from exc

            if not role_satisfies(principal.role, _required):
                raise PermissionError(
                    f"[IAM] Access denied: user={principal.user_id!r} "
                    f"role={principal.role.value!r} required={minimum_role!r}"
                )

            logger.debug(
                "[IAM] @iam_guard granted: user=%s role=%s fn=%s",
                principal.user_id, principal.role.value, fn.__name__,
            )
            # Inject principal into kwargs so the function can inspect the caller
            kwargs["_principal"] = principal
            return fn(*args, **kwargs)

        return wrapper
    return decorator
