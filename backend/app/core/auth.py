"""
PipelineSleuth Authentication & Operator Authorization
Enforces authenticated server-side principals for privileged workflows.
Credentials must be provisioned via environment configuration or secure provisioning APIs.
Hardcoded credentials and predictable defaults are strictly prohibited.
"""
import json
import logging
from typing import Optional, Dict, Any
from pydantic import BaseModel, Field, model_validator
from fastapi import Request, HTTPException, Depends
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials, APIKeyHeader
from backend.app.core.config import settings

logger = logging.getLogger(__name__)

MIN_OPERATOR_TOKEN_LENGTH = 16

# HTTP security schemes
bearer_scheme = HTTPBearer(auto_error=False)
api_key_header_scheme = APIKeyHeader(name="X-API-Key", auto_error=False)

# Blocklist of legacy, hardcoded, or insecure tokens that must never authenticate
PROHIBITED_TOKENS = frozenset({
    "ps_admin_token_master",
    "ps_viewer_token_guest",
    "ps_operator_sec_key_2026",
    "op_sec_token_dev_2026_admin",
    "op_sec_admin_token_2026_xyz",
    "<GENERATE_A_SECURE_RANDOM_TOKEN_HERE>",
    "<GENERATE_SECURE_TOKEN_32_BYTES>",
    "REPLACE_WITH_A_RANDOM_SECRET",
    "<REPLACE_WITH_A_RANDOM_SECRET>",
    "YOUR_SECURE_OPERATOR_API_KEY_HERE",
    "YOUR_TOKEN_HERE",
    "<YOUR_TOKEN_HERE>",
    "admin",
    "password",
    "secret",
    "human_operator",
    "default"
})


class AuthenticatedPrincipal(BaseModel):
    """Represents a verified, authenticated operator principal."""
    principal_id: str = Field(..., description="Unique immutable canonical ID of the authenticated user")
    username: str = Field(default="", description="Operator username or display identifier")
    role: str = Field(default="operator", description="Role of principal: 'operator', 'admin', 'viewer'")
    scopes: list[str] = Field(default_factory=list, description="Granted permission scopes")

    @model_validator(mode="before")
    @classmethod
    def populate_canonical_fields(cls, data: Any) -> Any:
        if isinstance(data, dict):
            # Check for canonical principal_id
            p_id = data.get("principal_id")
            if not p_id or not str(p_id).strip():
                raise ValueError("Principal definition must include a non-empty canonical 'principal_id'.")
            clean_pid = str(p_id).strip()
            data["principal_id"] = clean_pid
            if not data.get("username"):
                data["username"] = clean_pid
        return data


def validate_token_strength(token: str, token_name: str = "Operator token") -> str:
    """
    Validates that an operator credential token is non-empty, meets the minimum length,
    exhibits sufficient entropy, and is not on the prohibited blocklist.
    """
    if not token or not isinstance(token, str):
        raise ValueError(f"{token_name} must be a non-empty string.")
    cleaned = token.strip()
    if not cleaned:
        raise ValueError(f"{token_name} cannot be blank or whitespace-only.")
    if len(cleaned) < MIN_OPERATOR_TOKEN_LENGTH:
        raise ValueError(
            f"{token_name} is too short ({len(cleaned)} characters). "
            f"Must be at least {MIN_OPERATOR_TOKEN_LENGTH} characters for adequate entropy."
        )
    if cleaned in PROHIBITED_TOKENS:
        raise ValueError(f"Security error: {token_name} '{cleaned}' is on the prohibited credentials blocklist.")
    if "<" in cleaned or ">" in cleaned or "placeholder" in cleaned.lower() or "generate" in cleaned.lower() or "replace" in cleaned.lower():
        raise ValueError(f"Security error: {token_name} contains placeholder text. You must generate and deploy a real cryptographically secure random token.")
    if len(set(cleaned)) < 4:
        raise ValueError(f"{token_name} lacks sufficient character diversity/entropy.")
    return cleaned


def validate_operator_tokens_json(raw_json: str) -> Dict[str, AuthenticatedPrincipal]:
    """
    Parses and strictly validates OPERATOR_TOKENS_JSON configuration.
    Raises ValueError immediately on malformed JSON, invalid tokens, or missing fields.
    """
    if not raw_json or not raw_json.strip():
        return {}
    try:
        parsed = json.loads(raw_json)
    except json.JSONDecodeError as e:
        raise ValueError(f"Malformed OPERATOR_TOKENS_JSON: invalid JSON format ({e})") from e

    if not isinstance(parsed, dict):
        raise ValueError("Malformed OPERATOR_TOKENS_JSON: top-level structure must be a JSON object mapping tokens to principals.")

    validated: Dict[str, AuthenticatedPrincipal] = {}
    for token_str, principal_dict in parsed.items():
        clean_token = validate_token_strength(token_str, token_name="Token in OPERATOR_TOKENS_JSON")
        if not isinstance(principal_dict, dict):
            raise ValueError(f"Principal definition for token '{clean_token[:4]}...' must be a JSON object.")

        # Require canonical principal_id
        p_id = principal_dict.get("principal_id")
        if not p_id or not isinstance(p_id, str) or not p_id.strip():
            raise ValueError(f"Principal definition for token '{clean_token[:4]}...' is missing required non-blank 'principal_id'.")

        try:
            principal = AuthenticatedPrincipal(**principal_dict)
        except Exception as e:
            raise ValueError(f"Invalid principal definition for token '{clean_token[:4]}...': {e}") from e

        validated[clean_token] = principal
    return validated


def validate_all_operator_configuration() -> None:
    """
    Validates all environment-provisioned operator credentials during application startup.
    Fails startup with ValueError if any credential configuration is malformed, weak, or invalid.
    """
    if settings.OPERATOR_API_KEY:
        validate_token_strength(settings.OPERATOR_API_KEY, token_name="OPERATOR_API_KEY")

    if settings.OPERATOR_TOKENS_JSON:
        validate_operator_tokens_json(settings.OPERATOR_TOKENS_JSON)


# In-memory provisioned credential store (populated via env, startup provisioning, or test fixtures)
_DYNAMIC_OPERATOR_STORE: Dict[str, AuthenticatedPrincipal] = {}


def register_operator_credential(token: str, principal: AuthenticatedPrincipal) -> None:
    """
    Explicitly provisions an operator credential in the store.
    Rejects empty, trivial, or prohibited tokens.
    """
    clean_token = validate_token_strength(token, token_name="Operator credential token")
    _DYNAMIC_OPERATOR_STORE[clean_token] = principal


def clear_operator_credentials() -> None:
    """Clears dynamically registered credentials. Used primarily for test isolation."""
    _DYNAMIC_OPERATOR_STORE.clear()


def get_all_provisioned_operators() -> Dict[str, AuthenticatedPrincipal]:
    """
    Returns all valid operator credentials by combining environment configuration
    with dynamically registered credentials.
    """
    registry: Dict[str, AuthenticatedPrincipal] = {}

    # 1. Load from environment OPERATOR_API_KEY if configured and valid
    if settings.OPERATOR_API_KEY:
        try:
            env_token = validate_token_strength(settings.OPERATOR_API_KEY, token_name="OPERATOR_API_KEY")
            registry[env_token] = AuthenticatedPrincipal(
                principal_id="op_env_01",
                username="env_configured_operator",
                role="operator",
                scopes=["patch:approve", "patch:reject", "investigation:run"]
            )
        except ValueError as e:
            logger.error(f"Invalid OPERATOR_API_KEY: {e}")

    # 2. Load from environment OPERATOR_TOKENS_JSON if configured
    if settings.OPERATOR_TOKENS_JSON:
        try:
            parsed_tokens = validate_operator_tokens_json(settings.OPERATOR_TOKENS_JSON)
            registry.update(parsed_tokens)
        except ValueError as e:
            logger.error(f"Invalid OPERATOR_TOKENS_JSON: {e}")

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
