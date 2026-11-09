"""
PipelineSleuth Authentication & Operator Authorization
Enforces authenticated server-side principals for privileged workflows.
"""
from typing import Optional, Dict
from pydantic import BaseModel, Field
from fastapi import Request, HTTPException, Depends
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials, APIKeyHeader
from backend.app.core.config import settings

# HTTP security schemes
bearer_scheme = HTTPBearer(auto_error=False)
api_key_header_scheme = APIKeyHeader(name="X-API-Key", auto_error=False)


class AuthenticatedPrincipal(BaseModel):
    """Represents a verified, authenticated operator principal."""
    principal_id: str = Field(..., description="Unique immutable ID of the authenticated user")
    username: str = Field(..., description="Operator username or display identifier")
    role: str = Field(..., description="Role of principal: 'operator', 'admin', 'viewer'")
    scopes: list[str] = Field(default_factory=list, description="Granted permission scopes")


# Provisioned operator directory
DEFAULT_OPERATORS: Dict[str, AuthenticatedPrincipal] = {
    settings.OPERATOR_API_KEY: AuthenticatedPrincipal(
        principal_id="op_human_01",
        username="lead_operator",
        role="operator",
        scopes=["patch:approve", "patch:reject", "investigation:run"]
    ),
    "ps_admin_token_master": AuthenticatedPrincipal(
        principal_id="op_admin_00",
        username="security_admin",
        role="admin",
        scopes=["patch:approve", "patch:reject", "investigation:run", "admin:all"]
    ),
    "ps_viewer_token_guest": AuthenticatedPrincipal(
        principal_id="usr_viewer_09",
        username="auditor_viewer",
        role="viewer",
        scopes=["investigation:read"]
    )
}


def register_operator_credential(token: str, principal: AuthenticatedPrincipal) -> None:
    """Helper to provision or mock additional operator credentials (e.g. for testing)."""
    DEFAULT_OPERATORS[token] = principal


async def get_current_principal(
    bearer_creds: Optional[HTTPAuthorizationCredentials] = Depends(bearer_scheme),
    api_key_header: Optional[str] = Depends(api_key_header_scheme)
) -> Optional[AuthenticatedPrincipal]:
    """
    Extracts and authenticates a principal from Bearer token or X-API-Key header.
    Returns None if no credentials provided.
    """
    token: Optional[str] = None
    if bearer_creds and bearer_creds.credentials:
        token = bearer_creds.credentials
    elif api_key_header:
        token = api_key_header

    if not token:
        return None

    # Check against provisioned operators
    principal = DEFAULT_OPERATORS.get(token)
    return principal


async def require_operator_principal(
    request: Request,
    bearer_creds: Optional[HTTPAuthorizationCredentials] = Depends(bearer_scheme),
    api_key_header: Optional[str] = Depends(api_key_header_scheme)
) -> AuthenticatedPrincipal:
    """
    Strict dependency for endpoints requiring authenticated human operator authorization.
    Rejects unauthenticated requests (401) and unauthorized roles (403).
    """
    token: Optional[str] = None
    if bearer_creds and bearer_creds.credentials:
        token = bearer_creds.credentials
    elif api_key_header:
        token = api_key_header

    if not token:
        raise HTTPException(
            status_code=401,
            detail="Authentication required: No valid Bearer token or X-API-Key header supplied."
        )

    principal = DEFAULT_OPERATORS.get(token)
    if not principal:
        raise HTTPException(
            status_code=401,
            detail="Authentication failed: Invalid credentials or unrecognized operator token."
        )

    # Enforce role authorization
    if principal.role not in ("operator", "admin") or "patch:approve" not in principal.scopes:
        raise HTTPException(
            status_code=403,
            detail=f"Forbidden: Principal '{principal.username}' with role '{principal.role}' is not authorized to approve or reject patches."
        )

    return principal
