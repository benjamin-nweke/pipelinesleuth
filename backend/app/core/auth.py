"""
PipelineSleuth Authentication & Operator Authorization
Enforces authenticated server-side principals for privileged workflows.
Credentials must be provisioned via environment configuration or secure provisioning APIs.
Hardcoded credentials and predictable defaults are strictly prohibited.
"""
import json
import logging
from typing import Optional, Dict
from pydantic import BaseModel, Field
from fastapi import Request, HTTPException, Depends
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials, APIKeyHeader
from backend.app.core.config import settings

logger = logging.getLogger(__name__)

# HTTP security schemes
bearer_scheme = HTTPBearer(auto_error=False)
api_key_header_scheme = APIKeyHeader(name="X-API-Key", auto_error=False)

# Blocklist of legacy, hardcoded, or insecure tokens that must never authenticate
PROHIBITED_TOKENS = frozenset({
    "ps_admin_token_master",
    "ps_viewer_token_guest",
    "ps_operator_sec_key_2026",
    "admin",
    "password",
    "secret",
    "human_operator",
    "default"
})


class AuthenticatedPrincipal(BaseModel):
    """Represents a verified, authenticated operator principal."""
    principal_id: str = Field(..., description="Unique immutable ID of the authenticated user")
    username: str = Field(..., description="Operator username or display identifier")
    role: str = Field(..., description="Role of principal: 'operator', 'admin', 'viewer'")
    scopes: list[str] = Field(default_factory=list, description="Granted permission scopes")


# In-memory provisioned credential store (populated via env, startup provisioning, or test fixtures)
_DYNAMIC_OPERATOR_STORE: Dict[str, AuthenticatedPrincipal] = {}


def register_operator_credential(token: str, principal: AuthenticatedPrincipal) -> None:
    """
    Explicitly provisions an operator credential in the store.
    Rejects empty, trivial, or prohibited tokens.
    """
    token_clean = token.strip() if token else ""
    if not token_clean:
        raise ValueError("Cannot register empty operator credential token.")
    if token_clean in PROHIBITED_TOKENS:
        raise ValueError(f"Security error: Token '{token_clean}' is on the prohibited credentials blocklist.")
    _DYNAMIC_OPERATOR_STORE[token_clean] = principal


def clear_operator_credentials() -> None:
    """Clears dynamically registered credentials. Used primarily for test isolation."""
    _DYNAMIC_OPERATOR_STORE.clear()


def get_all_provisioned_operators() -> Dict[str, AuthenticatedPrincipal]:
    """
    Returns all valid operator credentials by combining environment configuration
    with dynamically registered credentials.
    """
    registry: Dict[str, AuthenticatedPrincipal] = {}

    # 1. Load from environment OPERATOR_API_KEY if configured and not prohibited
    if settings.OPERATOR_API_KEY:
        env_token = settings.OPERATOR_API_KEY.strip()
        if env_token and env_token not in PROHIBITED_TOKENS:
            registry[env_token] = AuthenticatedPrincipal(
                principal_id="op_env_01",
                username="env_configured_operator",
                role="operator",
                scopes=["patch:approve", "patch:reject", "investigation:run"]
            )

    # 2. Load from environment OPERATOR_TOKENS_JSON if configured
    if settings.OPERATOR_TOKENS_JSON:
        try:
            tokens_dict = json.loads(settings.OPERATOR_TOKENS_JSON)
            for t, p_data in tokens_dict.items():
                t_clean = t.strip()
                if t_clean and t_clean not in PROHIBITED_TOKENS and isinstance(p_data, dict):
                    registry[t_clean] = AuthenticatedPrincipal(**p_data)
        except Exception as e:
            logger.error(f"Failed to parse OPERATOR_TOKENS_JSON: {e}")

    # 3. Add dynamically registered credentials (e.g. from test fixtures or provisioning hooks)
    for t, p in _DYNAMIC_OPERATOR_STORE.items():
        if t not in PROHIBITED_TOKENS:
            registry[t] = p

    return registry


async def get_current_principal(
    bearer_creds: Optional[HTTPAuthorizationCredentials] = Depends(bearer_scheme),
    api_key_header: Optional[str] = Depends(api_key_header_scheme)
) -> Optional[AuthenticatedPrincipal]:
    """
    Extracts and authenticates a principal from Bearer token or X-API-Key header.
    Returns None if no credentials provided or if credentials are invalid.
    """
    token: Optional[str] = None
    if bearer_creds and bearer_creds.credentials:
        token = bearer_creds.credentials.strip()
    elif api_key_header:
        token = api_key_header.strip()

    if not token or token in PROHIBITED_TOKENS:
        return None

    provisioned = get_all_provisioned_operators()
    return provisioned.get(token)


async def require_operator_principal(
    request: Request,
    bearer_creds: Optional[HTTPAuthorizationCredentials] = Depends(bearer_scheme),
    api_key_header: Optional[str] = Depends(api_key_header_scheme)
) -> AuthenticatedPrincipal:
    """
    Strict dependency for endpoints requiring authenticated operator authorization.
    Rejects unauthenticated requests (401), unconfigured credentials (401),
    and unauthorized roles/scopes (403).
    """
    token: Optional[str] = None
    if bearer_creds and bearer_creds.credentials:
        token = bearer_creds.credentials.strip()
    elif api_key_header:
        token = api_key_header.strip()

    if not token:
        raise HTTPException(
            status_code=401,
            detail="Authentication required: No Bearer token or X-API-Key header supplied."
        )

    # Immediately reject prohibited tokens
    if token in PROHIBITED_TOKENS:
        raise HTTPException(
            status_code=401,
            detail="Authentication failed: Credential is invalid or on the security blocklist."
        )

    provisioned = get_all_provisioned_operators()
    if not provisioned:
        raise HTTPException(
            status_code=401,
            detail="Authentication unconfigured: No valid operator credentials have been provisioned on this server."
        )

    principal = provisioned.get(token)
    if not principal:
        raise HTTPException(
            status_code=401,
            detail="Authentication failed: Invalid credentials or unrecognized operator token."
        )

    # Enforce basic operator role or patch scopes
    has_patch_scope = any(scope in principal.scopes for scope in ("patch:approve", "patch:reject"))
    if principal.role not in ("operator", "admin") or not has_patch_scope:
        raise HTTPException(
            status_code=403,
            detail=f"Forbidden: Principal '{principal.username}' with role '{principal.role}' lacks operator privileges."
        )

    return principal
