"""Structured local and cloud model access using the Python standard library."""

import json
import os
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlparse
from urllib.request import Request, urlopen


class OllamaClient:
    def __init__(self, model=None, base_url=None, timeout=120, config=None):
        config = os.environ if config is None else config
        self.model = model or config.get("OLLAMA_MODEL") or config.get("OLLAMA_LLM_MODEL") or config.get("DEFAULT_LLM_MODEL") or "llama3.1:8b"
        self.base_url = (base_url or config.get("OLLAMA_BASE_URL", "http://localhost:11434")).rstrip("/")
        parsed = urlparse(self.base_url)
        if parsed.scheme not in ("http", "https") or not parsed.hostname or parsed.path:
            raise ValueError("Use the Ollama server URL, such as http://localhost:11434, without /v1.")
        self.timeout = timeout

    def decide(self, messages, schema):
        payload = {
            "model": self.model, "messages": messages, "format": schema,
            "stream": False, "options": {"temperature": 0},
        }
        request = Request(self.base_url + "/api/chat",
                          data=json.dumps(payload).encode(),
                          headers={"Content-Type": "application/json"}, method="POST")
        try:
            with urlopen(request, timeout=self.timeout) as response:
                body = json.load(response)
        except HTTPError as error:
            detail = error.read().decode(errors="replace")[:500]
            raise RuntimeError(f"Ollama request failed ({error.code}): {detail}") from error
        except (URLError, OSError) as error:
            raise RuntimeError(f"Cannot reach Ollama at {self.base_url}: {error}") from error
        try:
            decision = json.loads(body["message"]["content"])
        except (KeyError, TypeError, ValueError) as error:
            raise ValueError("Ollama did not return a valid decision. No analysis was started.") from error
        return decision


def cloud_error_message(provider, error):
    """Map recognized error codes to fixed guidance without echoing server text."""
    details = {}
    try:
        body = json.loads(error.read(16384))
        if isinstance(body, dict) and isinstance(body.get("error"), dict):
            details = body["error"]
    except (ValueError, OSError):
        pass
    finally:
        error.close()
    code = details.get("code")
    kind = details.get("type")
    code = code if isinstance(code, str) else ""
    kind = kind if isinstance(kind, str) else ""
    guidance = None
    if provider == "openai":
        known = {
            "credit_balance_exhausted": "No prepaid credits remain. Add credits in the provider billing settings.",
            "organization_spend_limit_exceeded": "The organization spend limit was reached. Review that limit in billing settings.",
            "project_spend_limit_exceeded": "The project spend limit was reached. Review that project's billing limit.",
            "organization_usage_limit_exceeded": "The organization usage limit was reached. Review usage limits with the provider.",
            "insufficient_quota": "API quota is unavailable. Check credits, billing, and usage limits before retrying.",
            "rate_limit_exceeded": "The request or token rate limit was reached. Wait before retrying and reduce the request rate.",
            "slow_down": "The request rate increased too quickly. Wait before retrying and reduce the request rate.",
            "model_not_found": "The model is unavailable or inaccessible. Check its name and your account's model access.",
        }
        guidance = known.get(code)
        if guidance is None and kind == "insufficient_quota":
            guidance = known["insufficient_quota"]
        if guidance is None and kind == "rate_limit_error":
            guidance = known["rate_limit_exceeded"]
    elif provider == "anthropic":
        if kind == "billing_error":
            guidance = "A billing problem was reported. Check credits and payment settings with the provider."
        elif kind == "rate_limit_error":
            guidance = "A rate or spending limit was reached. Check provider limits and billing before retrying."
    elif provider == "gemini":
        status = details.get("status")
        guidance = {
            "UNAUTHENTICATED": "Authentication failed. Check GEMINI_API_KEY.",
            "PERMISSION_DENIED": "Access was denied. Check the Gemini key, project permissions, and regional availability.",
            "RESOURCE_EXHAUSTED": "Gemini quota or rate limits were reached. Check project quota and billing before retrying.",
            "INVALID_ARGUMENT": "The request was rejected. Check the model, API key restrictions, and JSON schema support.",
            "NOT_FOUND": "The model is unavailable or inaccessible. Check the model name and project access.",
            "UNAVAILABLE": "Gemini is temporarily unavailable. Try again later.",
        }.get(status) if isinstance(status, str) else None
    if guidance is None:
        guidance = {
            400: "The request was rejected. Check the model name, supported settings, and JSON schema compatibility.",
            401: "Authentication failed. Check the API key and the account or project's authentication settings.",
            402: "A payment or billing problem was reported. Check provider billing settings.",
            403: "Access was denied. Check account permissions, project access, and regional restrictions.",
            404: "The requested resource is unavailable. Check the model name and your account's model access.",
            429: "A quota or rate limit was reported; the precise cause is unavailable. Check billing and usage limits.",
            500: "The provider reported a server error. Try again later.",
            503: "The provider is temporarily unavailable. Try again later.",
            504: "The provider request timed out. Try again later.",
            529: "The provider is overloaded. Try again later.",
        }.get(error.code, "The API request failed. Check provider status and request settings.")
    return f"{provider} request failed (HTTP {error.code}). {guidance} No automatic retry was attempted."


class CloudClient:
    """Use a selected provider's structured JSON response API."""

    def __init__(self, provider, model=None, timeout=120, config=None):
        config = os.environ if config is None else config
        if provider not in ("openai", "anthropic", "gemini"):
            raise ValueError("Choose ollama, openai, anthropic, or gemini.")
        self.provider = provider
        self.model = model or config.get(provider.upper() + "_MODEL")
        if not self.model:
            raise ValueError(f"Specify --model or {provider.upper()}_MODEL for {provider}.")
        self.api_key = config.get(provider.upper() + "_API_KEY", "").strip()
        if not self.api_key:
            raise ValueError(f"Set {provider.upper()}_API_KEY before starting. Do not put the key in a command argument.")
        if provider == "gemini":
            self.model = self.model.removeprefix("models/").strip()
            if not self.model:
                raise ValueError("Specify a Gemini model name.")
        self.base_url = {"openai": "https://api.openai.com/v1", "anthropic": "https://api.anthropic.com/v1",
                         "gemini": "https://generativelanguage.googleapis.com/v1beta"}[provider]
        self.timeout = timeout

    def decide(self, messages, schema):
        headers = {"Content-Type": "application/json"}
        if self.provider == "openai":
            path = "/responses"
            headers["Authorization"] = "Bearer " + self.api_key
            payload = {
                "model": self.model, "input": messages, "store": False,
                "text": {"format": {"type": "json_schema", "name": "analysis_decision",
                                    "strict": True, "schema": schema}},
            }
        elif self.provider == "anthropic":
            path = "/messages"
            headers.update({"x-api-key": self.api_key, "anthropic-version": "2023-06-01"})
            payload = {
                "model": self.model, "max_tokens": 2048,
                "system": "\n\n".join(m["content"] for m in messages if m["role"] == "system"),
                "messages": [m for m in messages if m["role"] != "system"],
                "output_config": {"format": {"type": "json_schema", "schema": schema}},
            }
        else:
            path = "/models/" + quote(self.model, safe="") + ":generateContent"
            headers["x-goog-api-key"] = self.api_key
            contents = []
            for message in messages:
                if message["role"] == "system":
                    continue
                if message["role"] not in ("user", "assistant"):
                    raise ValueError("Gemini text chat supports only system, user and assistant messages.")
                contents.append({"role": "model" if message["role"] == "assistant" else "user",
                                 "parts": [{"text": message["content"]}]})
            payload = {
                "systemInstruction": {"parts": [{"text": "\n\n".join(
                    m["content"] for m in messages if m["role"] == "system")}]},
                "contents": contents,
                "generationConfig": {"responseMimeType": "application/json", "responseJsonSchema": schema},
            }
        request = Request(self.base_url + path, data=json.dumps(payload).encode(),
                          headers=headers, method="POST")
        try:
            with urlopen(request, timeout=self.timeout) as response:
                body = json.load(response)
        except HTTPError as error:
            raise RuntimeError(cloud_error_message(self.provider, error)) from None
        except (URLError, OSError):
            raise RuntimeError(f"Cannot reach the {self.provider} API. No provider fallback was attempted.") from None
        try:
            if self.provider == "openai":
                if body.get("status") != "completed":
                    raise ValueError("The response was not completed.")
                blocks = [block for item in body["output"] if item.get("type") == "message"
                          for block in item["content"]]
                if any(block.get("type") == "refusal" for block in blocks):
                    raise ValueError("The provider refused the request.")
                text = "".join(block["text"] for block in blocks if block.get("type") == "output_text")
            elif self.provider == "anthropic":
                if body.get("stop_reason") != "end_turn":
                    raise ValueError("The response was interrupted or refused.")
                text = "".join(block["text"] for block in body["content"] if block.get("type") == "text")
            else:
                if body.get("promptFeedback", {}).get("blockReason"):
                    raise ValueError("The provider blocked the request.")
                candidates = body["candidates"]
                if len(candidates) != 1 or candidates[0].get("finishReason") != "STOP":
                    raise ValueError("The response was interrupted or refused.")
                parts = candidates[0]["content"]["parts"]
                if any("functionCall" in part for part in parts):
                    raise ValueError("Tool calls are not supported by this client.")
                text = "".join(part["text"] for part in parts if "text" in part and not part.get("thought"))
            return json.loads(text)
        except (AttributeError, KeyError, TypeError, ValueError):
            raise ValueError(f"{self.provider} did not return a complete JSON decision. No analysis was started.") from None
