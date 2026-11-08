"""
AI Model Provider Abstraction and Capability Adapter
Supports Ollama local endpoints and OpenAI-compatible endpoints with strict Pydantic JSON validation.
Never simulates AI responses: communicates connection issues honestly.
"""
import json
import re
import time
from typing import Dict, Any, Optional, Tuple
import httpx
from pydantic import ValidationError
from backend.app.core.config import settings
from backend.app.schemas.agent import AgentDecision


class ModelProviderError(Exception):
    """Raised when the AI model provider encounters a fatal error or is unreachable."""
    pass


class AIProvider:
    def __init__(
        self,
        provider_type: Optional[str] = None,
        ollama_base_url: Optional[str] = None,
        ollama_model: Optional[str] = None,
        openai_base_url: Optional[str] = None,
        openai_api_key: Optional[str] = None,
        openai_model: Optional[str] = None
    ):
        self.provider_type = provider_type or settings.AI_PROVIDER
        self.ollama_base_url = (ollama_base_url or settings.OLLAMA_BASE_URL).rstrip("/")
        self.ollama_model = ollama_model or settings.OLLAMA_MODEL
        self.openai_base_url = (openai_base_url or settings.OPENAI_BASE_URL).rstrip("/")
        self.openai_api_key = openai_api_key or settings.OPENAI_API_KEY
        self.openai_model = openai_model or settings.OPENAI_MODEL

    async def test_connectivity(self) -> Tuple[bool, str, int]:
        """
        Tests connectivity to the currently configured provider and model.
        Returns: (success, status_message, latency_ms)
        """
        start = time.time()
        client = httpx.AsyncClient(timeout=10.0)
        try:
            if self.provider_type == "ollama":
                # Check /api/tags to verify Ollama daemon is running
                resp = await client.get(f"{self.ollama_base_url}/api/tags")
                latency = int((time.time() - start) * 1000)
                if resp.status_code == 200:
                    models = resp.json().get("models", [])
                    model_names = [m.get("name") for m in models]
                    if any(self.ollama_model in name for name in model_names):
                        return True, f"Ollama is running. Model '{self.ollama_model}' is available.", latency
                    else:
                        avail = ", ".join(model_names) if model_names else "none"
                        return False, f"Ollama is running, but model '{self.ollama_model}' is not pulled. Available: [{avail}]. Run `ollama pull {self.ollama_model}`.", latency
                else:
                    return False, f"Ollama endpoint returned HTTP {resp.status_code}: {resp.text[:100]}", latency
            else:
                # OpenAI compatible endpoint
                headers = {"Authorization": f"Bearer {self.openai_api_key}"} if self.openai_api_key else {}
                resp = await client.get(f"{self.openai_base_url}/models", headers=headers)
                latency = int((time.time() - start) * 1000)
                if resp.status_code == 200:
                    return True, f"OpenAI-compatible endpoint reachable. Model: '{self.openai_model}'.", latency
                else:
                    return False, f"OpenAI endpoint returned HTTP {resp.status_code}: {resp.text[:100]}", latency
        except httpx.ConnectError:
            latency = int((time.time() - start) * 1000)
            if self.provider_type == "ollama":
                return False, f"Cannot connect to Ollama at {self.ollama_base_url}. Is Ollama installed and running? Run `ollama serve`.", latency
            else:
                return False, f"Cannot connect to OpenAI endpoint at {self.openai_base_url}.", latency
        except Exception as e:
            latency = int((time.time() - start) * 1000)
            return False, f"Connectivity error: {str(e)}", latency
        finally:
            await client.aclose()

    def _extract_json_block(self, text: str) -> Dict[str, Any]:
        """
        Extracts and parses JSON object from model output, handling markdown code fences.
        """
        text = text.strip()
        # Look for ```json ... ``` or ``` ... ```
        fence_match = re.search(r'```(?:json)?\s*([\s\S]*?)\s*```', text)
        if fence_match:
            candidate = fence_match.group(1).strip()
            try:
                return json.loads(candidate)
            except json.JSONDecodeError:
                pass

        # Try searching for outermost curly braces { ... }
        brace_match = re.search(r'(\{[\s\S]*\})', text)
        if brace_match:
            candidate = brace_match.group(1).strip()
            try:
                return json.loads(candidate)
            except json.JSONDecodeError:
                pass

        # Last resort direct parse
        return json.loads(text)

    async def generate_decision(
        self,
        prompt: str,
        system_instruction: str,
        temperature: float = 0.2
    ) -> AgentDecision:
        """
        Invokes the AI model and validates the response against the AgentDecision Pydantic schema.
        Never fabricates intelligence: raises ModelProviderError if model is unreachable.
        """
        client = httpx.AsyncClient(timeout=120.0)
        raw_response_text = ""

        try:
            if self.provider_type == "ollama":
                payload = {
                    "model": self.ollama_model,
                    "prompt": prompt,
                    "system": system_instruction,
                    "stream": False,
                    "format": "json",
                    "options": {
                        "temperature": temperature,
                        "num_ctx": 8192
                    }
                }
                resp = await client.post(f"{self.ollama_base_url}/api/generate", json=payload)
                if resp.status_code != 200:
                    raise ModelProviderError(f"Ollama API returned HTTP {resp.status_code}: {resp.text[:200]}")
                data = resp.json()
                raw_response_text = data.get("response", "")
            else:
                # OpenAI compatible endpoint
                headers = {
                    "Content-Type": "application/json"
                }
                if self.openai_api_key:
                    headers["Authorization"] = f"Bearer {self.openai_api_key}"
                
                payload = {
                    "model": self.openai_model,
                    "messages": [
                        {"role": "system", "content": system_instruction},
                        {"role": "user", "content": prompt}
                    ],
                    "temperature": temperature,
                    "response_format": {"type": "json_object"}
                }
                resp = await client.post(f"{self.openai_base_url}/chat/completions", headers=headers, json=payload)
                if resp.status_code != 200:
                    raise ModelProviderError(f"OpenAI-compatible API returned HTTP {resp.status_code}: {resp.text[:200]}")
                data = resp.json()
                choices = data.get("choices", [])
                if not choices:
                    raise ModelProviderError("No completion choices returned by model provider.")
                raw_response_text = choices[0].get("message", {}).get("content", "")

        except httpx.ConnectError as ce:
            raise ModelProviderError(
                f"Connection failed to {self.provider_type} provider at "
                f"'{self.ollama_base_url if self.provider_type == 'ollama' else self.openai_base_url}'. "
                f"Please ensure the service is running and accessible."
            ) from ce
        except httpx.TimeoutException as te:
            raise ModelProviderError(f"Model inference timed out after 120s.") from te
        except Exception as e:
            if isinstance(e, ModelProviderError):
                raise e
            raise ModelProviderError(f"Error calling AI provider: {str(e)}") from e
        finally:
            await client.aclose()

        # Extract and parse structured JSON
        try:
            parsed_json = self._extract_json_block(raw_response_text)
        except Exception as e:
            raise ModelProviderError(
                f"Model response was not valid JSON: {str(e)}. Raw snippet: {raw_response_text[:300]}"
            )

        # Validate with Pydantic
        try:
            decision = AgentDecision(**parsed_json)
            return decision
        except ValidationError as ve:
            raise ModelProviderError(
                f"Model output failed schema validation: {str(ve)}. Parsed JSON: {parsed_json}"
            )


default_provider = AIProvider()

