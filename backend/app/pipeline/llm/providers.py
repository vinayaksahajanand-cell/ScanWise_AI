"""Vision-model providers over plain HTTP (no vendor SDK lock-in).

* ``openai`` / ``nvidia`` / ``compatible``: OpenAI Chat Completions format
  (OpenAI, NVIDIA NIM / build.nvidia.com, OpenRouter, vLLM, Ollama, LM Studio...).
* ``gemini``: Google Generative Language API (generateContent).
* ``anthropic``: Anthropic Messages API (structured output via a forced tool call).

API keys come from environment variables only and are never logged.
"""

from __future__ import annotations

import base64
import json
import logging
import random
import re
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass

import httpx

from app.config import Settings

from .schema import SCHEMA_DSL, to_gemini_schema, to_json_schema

log = logging.getLogger(__name__)


class ProviderError(RuntimeError):
    pass


# A 429 that means "your account is out of credit" can never succeed on retry; retrying only
# makes every document wait through the backoff before falling back to OCR.
_QUOTA_EXHAUSTED = re.compile(r"insufficient_quota|credit_balance_exhausted|no credits remaining", re.I)


# Reasoning models (o-series, gpt-5, gpt-6, ...) take reasoning_effort and reject temperature != 1.
_REASONING_MODEL = re.compile(r"^(o\d|gpt-[5-9]|gpt-\d{2,})", re.I)
_EFFORT_PREFERENCE = ("low", "minimal", "none", "medium", "high")

# What each endpoint+model accepts, learned from 400 responses. Providers are built per scan, so without
# this memory every scan would repeat the failed request (a wasted round trip) before the real one.
_CAPS: dict[tuple[str, str, str], dict] = {}


@dataclass
class ProviderInfo:
    name: str
    model: str
    configured: bool
    pages_per_call: int


def parse_json_loose(text: str) -> dict:
    s = text.strip()
    s = re.sub(r"^```(?:json)?\s*", "", s)
    s = re.sub(r"\s*```$", "", s)
    try:
        return json.loads(s)
    except json.JSONDecodeError:
        start, end = s.find("{"), s.rfind("}")
        if start >= 0 and end > start:
            return json.loads(s[start : end + 1])
        raise


class VisionProvider(ABC):
    name = "base"

    def __init__(self, model: str, timeout: float, max_retries: int, pages_per_call: int):
        self.model = model
        self.timeout = timeout
        self.max_retries = max_retries
        self.pages_per_call = pages_per_call

    @property
    def label(self) -> str:
        return f"{self.name}/{self.model}"

    def _post(self, url: str, headers: dict, payload: dict) -> dict:
        last: Exception | None = None
        for attempt in range(self.max_retries + 1):
            try:
                # long read timeout (the model may think), short connect timeout: an unreachable API / failing DNS
                # must fall back to OCR in seconds, not after minutes
                with httpx.Client(timeout=httpx.Timeout(self.timeout, connect=min(15.0, self.timeout))) as client:
                    r = client.post(url, headers=headers, json=payload)
                if r.status_code == 429 and _QUOTA_EXHAUSTED.search(r.text):
                    raise ProviderError(f"{self.name} HTTP 429: {r.text[:300]}")
                if r.status_code in (408, 409, 429, 500, 502, 503, 504, 529):
                    retry_after = r.headers.get("retry-after")
                    delay = float(retry_after) if retry_after and retry_after.replace(".", "").isdigit() else (2 ** attempt) + random.random()
                    last = ProviderError(f"{self.name} HTTP {r.status_code}: {r.text[:300]}")
                    time.sleep(min(delay, 30))
                    continue
                if r.status_code >= 400:
                    raise ProviderError(f"{self.name} HTTP {r.status_code}: {r.text[:600]}")
                return r.json()
            except (httpx.TimeoutException, httpx.TransportError) as e:
                last = ProviderError(f"{self.name} network error: {e}")
                time.sleep(min((2 ** attempt) + random.random(), 30))
        raise last or ProviderError(f"{self.name}: request failed")

    @abstractmethod
    def complete(self, system: str, messages: list[tuple[str, str]], images: list[bytes],
                 schema: dict | None = None, schema_name: str = "document_extraction") -> dict:
        """messages: list of (role, text). Images (JPEG) are attached to the first user message.

        ``schema`` is a node of the schema DSL in ``schema.py`` describing the JSON to return;
        ``None`` means the invoice/document extraction schema (the original behaviour).
        """


class OpenAICompatibleProvider(VisionProvider):
    def __init__(self, name: str, base_url: str, api_key: str | None, model: str, timeout: float, max_retries: int, pages_per_call: int,
                 reasoning_effort: str | None = None):
        super().__init__(model, timeout, max_retries, pages_per_call)
        self.name = name
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        caps = _CAPS.get((name, self.base_url, model), {})
        self._format_mode = caps.get("format_mode", "json_schema")
        self._send_temperature = caps.get("temperature", True)
        self.reasoning_effort = caps.get("effort", (reasoning_effort or "").strip().lower() or None)
        self._send_reasoning_effort = caps.get("send_effort", True)

    def _remember(self) -> None:
        _CAPS[(self.name, self.base_url, self.model)] = {
            "format_mode": self._format_mode, "temperature": self._send_temperature,
            "effort": self.reasoning_effort, "send_effort": self._send_reasoning_effort,
        }

    def _messages(self, system: str, messages: list[tuple[str, str]], images: list[bytes]) -> list[dict]:
        out: list[dict] = [{"role": "system", "content": system}]
        for i, (role, text) in enumerate(messages):
            if i == 0 and role == "user":
                content: list[dict] = []
                for img in images:
                    url = "data:image/jpeg;base64," + base64.b64encode(img).decode()
                    part: dict = {"type": "image_url", "image_url": {"url": url}}
                    if self.name == "openai":
                        part["image_url"]["detail"] = "high"
                    content.append(part)
                content.append({"type": "text", "text": text})
                out.append({"role": "user", "content": content})
            else:
                out.append({"role": role, "content": text})
        return out

    def complete(self, system: str, messages: list[tuple[str, str]], images: list[bytes],
                 schema: dict | None = None, schema_name: str = "document_extraction") -> dict:
        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        payload: dict = {"model": self.model, "messages": self._messages(system, messages, images)}
        reasoning = _REASONING_MODEL.match(self.model or "")
        if not reasoning and self._send_temperature:
            payload["temperature"] = 0
        if reasoning and self.reasoning_effort and self._send_reasoning_effort:
            # Reasoning models "think" before answering (tens of seconds at the default effort).
            # Transcribing a page doesn't need that; values are cross-checked against OCR and arithmetic.
            payload["reasoning_effort"] = self.reasoning_effort
        if self.name == "openai":
            payload["max_completion_tokens"] = 16000
        else:
            payload["max_tokens"] = 8192
        modes = ["json_schema", "json_object", "none"]
        start = modes.index(self._format_mode)
        last_err: Exception | None = None
        for mode in modes[start:]:
            p = dict(payload)
            if mode == "json_schema":
                p["response_format"] = {"type": "json_schema",
                                        "json_schema": {"name": schema_name, "strict": True, "schema": to_json_schema(schema or SCHEMA_DSL)}}
            elif mode == "json_object":
                p["response_format"] = {"type": "json_object"}
            try:
                data = self._post(f"{self.base_url}/chat/completions", headers, p)
            except ProviderError as e:
                msg = str(e)
                if " 400" in msg and "reasoning_effort" in msg and "reasoning_effort" in payload:
                    # "Supported values are: 'none', 'low', ..." -> use the closest supported one; otherwise stop sending it
                    listed = re.findall(r"'(none|minimal|low|medium|high|xhigh)'", msg.split("Supported values", 1)[1]) if "Supported values" in msg else []
                    alt = next((v for v in _EFFORT_PREFERENCE if v in listed and v != self.reasoning_effort), None)
                    if alt:
                        self.reasoning_effort = alt
                    else:
                        self._send_reasoning_effort = False
                    self._remember()
                    return self.complete(system, messages, images, schema, schema_name)
                if " 400" in msg and re.search(r"response_format|json_schema|schema|structured", msg, re.I):
                    last_err = e
                    continue
                if " 400" in msg and "temperature" in msg and "temperature" in payload:
                    self._send_temperature = False
                    self._remember()
                    return self.complete(system, messages, images, schema, schema_name)
                raise
            if mode != self._format_mode:
                self._format_mode = mode
                self._remember()
            try:
                text = data["choices"][0]["message"]["content"]
            except (KeyError, IndexError, TypeError) as e:
                raise ProviderError(f"{self.name}: unexpected response shape") from e
            if not text:
                raise ProviderError(f"{self.name}: empty response (finish_reason={data['choices'][0].get('finish_reason')})")
            return parse_json_loose(text)
        raise last_err or ProviderError(f"{self.name}: structured output not supported")


class GeminiProvider(VisionProvider):
    name = "gemini"

    def __init__(self, base_url: str, api_key: str, model: str, timeout: float, max_retries: int, pages_per_call: int):
        super().__init__(model, timeout, max_retries, pages_per_call)
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self._use_schema = True

    def complete(self, system: str, messages: list[tuple[str, str]], images: list[bytes],
                 schema: dict | None = None, schema_name: str = "document_extraction") -> dict:
        contents = []
        for i, (role, text) in enumerate(messages):
            parts: list[dict] = []
            if i == 0:
                parts.extend({"inline_data": {"mime_type": "image/jpeg", "data": base64.b64encode(img).decode()}} for img in images)
            parts.append({"text": text})
            contents.append({"role": "model" if role == "assistant" else "user", "parts": parts})
        gen: dict = {"temperature": 0, "responseMimeType": "application/json", "maxOutputTokens": 16384}
        if self._use_schema:
            gen["responseSchema"] = to_gemini_schema(schema or SCHEMA_DSL)
        payload = {"contents": contents, "systemInstruction": {"parts": [{"text": system}]}, "generationConfig": gen}
        url = f"{self.base_url}/models/{self.model}:generateContent"
        headers = {"Content-Type": "application/json", "x-goog-api-key": self.api_key}
        try:
            data = self._post(url, headers, payload)
        except ProviderError as e:
            if self._use_schema and " 400" in str(e):
                self._use_schema = False
                return self.complete(system, messages, images, schema, schema_name)
            raise
        try:
            cand = data["candidates"][0]
            text = "".join(p.get("text", "") for p in cand["content"]["parts"])
        except (KeyError, IndexError, TypeError) as e:
            raise ProviderError(f"gemini: unexpected response ({str(data)[:200]})") from e
        return parse_json_loose(text)


class AnthropicProvider(VisionProvider):
    name = "anthropic"

    def __init__(self, base_url: str, api_key: str, model: str, timeout: float, max_retries: int, pages_per_call: int):
        super().__init__(model, timeout, max_retries, pages_per_call)
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key

    def complete(self, system: str, messages: list[tuple[str, str]], images: list[bytes],
                 schema: dict | None = None, schema_name: str = "document_extraction") -> dict:
        msgs = []
        for i, (role, text) in enumerate(messages):
            if i == 0:
                content: list[dict] = [
                    {"type": "image", "source": {"type": "base64", "media_type": "image/jpeg", "data": base64.b64encode(img).decode()}}
                    for img in images
                ]
                content.append({"type": "text", "text": text})
                msgs.append({"role": "user", "content": content})
            else:
                msgs.append({"role": role, "content": text})
        payload = {
            "model": self.model,
            "max_tokens": 16000,
            "temperature": 0,
            "system": system,
            "messages": msgs,
            "tools": [{
                "name": "record_extraction",
                "description": "Record the transcribed document data.",
                "input_schema": to_json_schema(schema or SCHEMA_DSL),
            }],
            "tool_choice": {"type": "tool", "name": "record_extraction"},
        }
        headers = {"x-api-key": self.api_key, "anthropic-version": "2023-06-01", "content-type": "application/json"}
        data = self._post(f"{self.base_url}/messages", headers, payload)
        for block in data.get("content", []):
            if block.get("type") == "tool_use":
                return block.get("input") or {}
        text = "".join(b.get("text", "") for b in data.get("content", []) if b.get("type") == "text")
        if text:
            return parse_json_loose(text)
        raise ProviderError(f"anthropic: no tool output (stop_reason={data.get('stop_reason')})")


def configured_providers(s: Settings) -> list[ProviderInfo]:
    return [
        ProviderInfo("openai", s.openai_model, bool(s.openai_api_key), s.llm_pages_per_call),
        ProviderInfo("gemini", s.gemini_model, bool(s.gemini_api_key), s.llm_pages_per_call),
        ProviderInfo("anthropic", s.anthropic_model, bool(s.anthropic_api_key), s.llm_pages_per_call),
        ProviderInfo("nvidia", s.nvidia_model, bool(s.nvidia_api_key), s.nvidia_pages_per_call),
        ProviderInfo("compatible", s.compatible_model or "", bool(s.compatible_base_url and s.compatible_model), s.llm_pages_per_call),
    ]


def build_provider(s: Settings, name: str | None = None) -> VisionProvider | None:
    choice = (name or s.llm_provider or "auto").lower()
    if choice in ("none", "off", ""):
        return None
    if choice == "auto":
        for info in configured_providers(s):
            if info.configured:
                choice = info.name
                break
        else:
            return None
    t, r = s.llm_timeout_s, s.llm_max_retries
    if choice == "openai" and s.openai_api_key:
        return OpenAICompatibleProvider("openai", s.openai_base_url, s.openai_api_key, s.openai_model, t, r, s.llm_pages_per_call,
                                        reasoning_effort=s.openai_reasoning_effort)
    if choice == "nvidia" and s.nvidia_api_key:
        return OpenAICompatibleProvider("nvidia", s.nvidia_base_url, s.nvidia_api_key, s.nvidia_model, t, r, s.nvidia_pages_per_call)
    if choice == "compatible" and s.compatible_base_url and s.compatible_model:
        return OpenAICompatibleProvider("compatible", s.compatible_base_url, s.compatible_api_key, s.compatible_model, t, r, s.llm_pages_per_call)
    if choice == "gemini" and s.gemini_api_key:
        return GeminiProvider(s.gemini_base_url, s.gemini_api_key, s.gemini_model, t, r, s.llm_pages_per_call)
    if choice == "anthropic" and s.anthropic_api_key:
        return AnthropicProvider(s.anthropic_base_url, s.anthropic_api_key, s.anthropic_model, t, r, s.llm_pages_per_call)
    return None
