"""Groq chat-completions client used by the AI Control System.

Groq exposes an OpenAI-compatible endpoint (``https://api.groq.com/openai/v1``),
so the request/response shape is unchanged; only the endpoint, the default model
and the key name differ.

The client is deliberately small: one endpoint, strict timeouts, and clear
errors. When no API key is configured, or the provider errors out, callers get
:class:`AIUnavailable` and fall back to human escalation instead of inventing
an answer.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
from typing import Any

import aiohttp

from utils.ai import settings as ai_settings

logger = logging.getLogger("discord")

USER_AGENT = "DiscordBot (https://github.com/Rapptz/discord.py 2.7.1)"
JSON_BLOCK = re.compile(r"```(?:json)?\s*(?P<body>.*?)```", re.DOTALL)


class AIUnavailable(RuntimeError):
    """The provider cannot be used; the message is safe to show to administrators."""


def parse_json_block(text: str) -> dict:
    """Pull the first JSON object out of a model reply."""

    if not text:
        return {}
    candidate = text.strip()
    match = JSON_BLOCK.search(candidate)
    if match:
        candidate = match.group("body").strip()
    try:
        parsed = json.loads(candidate)
    except ValueError:
        start, end = candidate.find("{"), candidate.rfind("}")
        if start == -1 or end <= start:
            return {}
        try:
            parsed = json.loads(candidate[start : end + 1])
        except ValueError:
            return {}
    return parsed if isinstance(parsed, dict) else {}


class AIProvider:
    """One configured model endpoint."""

    def __init__(self, settings: dict[str, Any], *, api_key: str | None = None) -> None:
        self.settings = settings
        self.api_key = (api_key if api_key is not None else ai_settings.api_key()).strip()
        self.base_url = str(settings.get("base_url") or "https://api.groq.com/openai/v1").rstrip("/")
        self.model = str(settings.get("model") or "openai/gpt-oss-120b")
        self.timeout = max(10.0, float(settings.get("max_processing_seconds") or 90))
        self.temperature = float(settings.get("temperature") or 0.2)
        self.max_tokens = max(64, int(float(settings.get("max_output_tokens") or 700)))

    @property
    def configured(self) -> bool:
        return bool(self.api_key) and bool(self.model)

    async def complete(self, prompt: str, *, system: str | None = None, json_mode: bool = False) -> str:
        if not self.configured:
            raise AIUnavailable(
                "No AI provider API key is configured. Add AI_API_KEY in the app's secrets to enable AI answers."
            )
        messages = []
        if system:
            messages.append({"role": "system", "content": system})
        messages.append({"role": "user", "content": prompt})
        payload: dict[str, Any] = {
            "model": self.model,
            "messages": messages,
            "temperature": self.temperature,
            "max_tokens": self.max_tokens,
        }
        if json_mode:
            payload["response_format"] = {"type": "json_object"}
        data = await self._post("/chat/completions", payload)
        try:
            return str(data["choices"][0]["message"]["content"] or "")
        except (KeyError, IndexError, TypeError) as error:
            raise AIUnavailable("The AI provider returned an unexpected response.") from error

    async def complete_json(self, prompt: str, *, system: str | None = None) -> dict:
        text = await self.complete(prompt, system=system, json_mode=True)
        return parse_json_block(text)

    async def test(self) -> dict[str, Any]:
        """Round-trip check used by the dashboard's connection test."""

        if not self.api_key:
            return {
                "ok": False,
                "error": "AI_API_KEY is not set. Add it in Secrets, then test again.",
                "model": self.model,
                "base_url": self.base_url,
            }
        started = asyncio.get_running_loop().time()
        try:
            data = await self._post(
                "/chat/completions",
                {
                    "model": self.model,
                    "messages": [{"role": "user", "content": "Reply with the single word: ready"}],
                    # Reasoning models spend tokens before the answer, so leave room for both.
                    "max_tokens": 64,
                    "temperature": 0,
                },
            )
        except AIUnavailable as error:
            return {"ok": False, "error": str(error), "model": self.model, "base_url": self.base_url}
        latency_ms = int((asyncio.get_running_loop().time() - started) * 1000)
        reply = ""
        try:
            reply = str(data["choices"][0]["message"]["content"] or "").strip()[:80]
        except (KeyError, IndexError, TypeError):
            pass
        return {"ok": True, "model": self.model, "base_url": self.base_url, "latency_ms": latency_ms, "reply": reply}

    async def _post(self, path: str, payload: dict[str, Any]) -> dict:
        url = f"{self.base_url}{path}"
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
            "User-Agent": USER_AGENT,
        }
        try:
            async with aiohttp.ClientSession(headers={"User-Agent": USER_AGENT}) as session:
                async with session.post(
                    url,
                    json=payload,
                    headers=headers,
                    timeout=aiohttp.ClientTimeout(total=self.timeout),
                ) as response:
                    body = await response.text()
                    if response.status >= 400:
                        raise AIUnavailable(friendly_error(response.status, body))
                    try:
                        return json.loads(body)
                    except ValueError as error:
                        raise AIUnavailable("The AI provider returned a response that was not JSON.") from error
        except asyncio.TimeoutError as error:
            raise AIUnavailable(
                f"The AI provider did not answer within {int(self.timeout)} seconds."
            ) from error
        except aiohttp.ClientError as error:
            raise AIUnavailable(f"Could not reach the AI provider: {error}") from error


def friendly_error(status: int, body: str) -> str:
    detail = ""
    try:
        payload = json.loads(body)
        error = payload.get("error")
        if isinstance(error, dict):
            detail = str(error.get("message") or "")
        elif error:
            detail = str(error)
    except (ValueError, AttributeError):
        detail = ""
    if status in (401, 403):
        return "The AI provider rejected the API key. Update AI_API_KEY in Secrets."
    if status == 404:
        return "The AI provider could not find that model. Check the model name and base URL."
    if status == 429:
        return "The AI provider is rate limiting requests. Try again in a moment."
    if status >= 500:
        return "The AI provider is currently unavailable. The bot keeps working with human escalation."
    return detail or f"The AI provider returned HTTP {status}."
