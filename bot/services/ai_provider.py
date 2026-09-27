"""Shared server-side client for the configured AI provider."""

import asyncio
import datetime
import json
import logging
import os
import time

import aiohttp

BASE_URL = "https://api.atria-asi.ai/v1/chat/completions"
MODEL = "Atria-Dawn-Preview"
MAX_RESPONSE_BYTES = 1024 * 1024
logger = logging.getLogger(__name__)


class AIProvider:
    def __init__(self):
        self._session = None
        self._semaphore = asyncio.Semaphore(3)
        self._rate_lock = asyncio.Lock()
        self._last_call = 0.0
        self._minimum_interval = 0.35
        self._status = {
            "provider": "Atria",
            "model": MODEL,
            "configured": False,
            "status": "Not Configured",
            "latency_ms": None,
            "last_request": None,
            "error": None,
        }

    @property
    def api_key(self):
        return os.getenv("AI_API_KEY") or os.getenv("ATRIA_API_KEY")

    def status(self):
        configured = bool(self.api_key)
        result = dict(self._status)
        result["configured"] = configured
        if not configured:
            result["status"] = "Not Configured"
            result["error"] = "AI_API_KEY is not configured"
        elif result["status"] == "Not Configured":
            result["status"] = "Not Tested"
            result["error"] = None
        return result

    async def open(self):
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession(
                timeout=aiohttp.ClientTimeout(total=25, connect=5, sock_read=20),
                raise_for_status=False,
            )

    async def close(self):
        if self._session is not None and not self._session.closed:
            await self._session.close()

    async def complete(self, messages, max_tokens=1200):
        key = self.api_key
        now = datetime.datetime.now(datetime.timezone.utc).isoformat()
        self._status["last_request"] = now
        self._status["latency_ms"] = None

        if not key:
            self._status.update(status="Not Configured", error="AI_API_KEY is not configured")
            raise RuntimeError("AI service is not configured")
        if not isinstance(messages, list) or not 1 <= len(messages) <= 50:
            raise ValueError("Invalid AI request")
        if not isinstance(max_tokens, int) or isinstance(max_tokens, bool) or not 1 <= max_tokens <= 2000:
            raise ValueError("Invalid AI request")

        await self.open()
        started = time.monotonic()
        async with self._semaphore:
            async with self._rate_lock:
                wait = max(0.0, self._minimum_interval - (time.monotonic() - self._last_call))
                if wait:
                    await asyncio.sleep(wait)
                self._last_call = time.monotonic()
            try:
                async with self._session.post(
                    BASE_URL,
                    headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
                    json={"model": MODEL, "messages": messages, "max_tokens": max_tokens},
                ) as response:
                    if response.content_type != "application/json":
                        raise RuntimeError("AI provider returned an invalid response")
                    if response.content_length is not None and response.content_length > MAX_RESPONSE_BYTES:
                        raise RuntimeError("AI provider response is too large")
                    raw = await response.content.read(MAX_RESPONSE_BYTES + 1)
                    if len(raw) > MAX_RESPONSE_BYTES:
                        raise RuntimeError("AI provider response is too large")
                    data = json.loads(raw)
                    self._status["latency_ms"] = round((time.monotonic() - started) * 1000)
                    if response.status >= 400:
                        self._status.update(
                            status="Error",
                            error=f"AI provider returned HTTP {response.status}",
                        )
                        logger.warning("AI provider request failed with HTTP %s", response.status)
                        raise RuntimeError("AI provider request failed")
                    choices = data.get("choices") if isinstance(data, dict) else None
                    first = choices[0] if isinstance(choices, list) and choices else {}
                    message = first.get("message", {}) if isinstance(first, dict) else {}
                    content = message.get("content") if isinstance(message, dict) else None
                    if not isinstance(content, str) or not content:
                        raise RuntimeError("AI provider returned an invalid response")
                    self._status.update(status="Connected", error=None)
                    return content
            except (aiohttp.ClientError, asyncio.TimeoutError, json.JSONDecodeError) as exc:
                self._status["latency_ms"] = round((time.monotonic() - started) * 1000)
                self._status.update(status="Error", error=type(exc).__name__)
                logger.warning("AI provider request failed: %s", type(exc).__name__)
                raise RuntimeError("AI service is temporarily unavailable") from None
            except RuntimeError as exc:
                self._status["latency_ms"] = round((time.monotonic() - started) * 1000)
                if self._status["status"] != "Error":
                    self._status.update(status="Error", error=str(exc)[:120])
                raise

    async def test_connection(self):
        if not self.api_key:
            self._status["last_request"] = datetime.datetime.now(datetime.timezone.utc).isoformat()
            self._status.update(
                configured=False,
                status="Not Configured",
                latency_ms=None,
                error="AI_API_KEY is not configured",
            )
            return self.status()
        try:
            await self.complete(
                [
                    {"role": "system", "content": "Reply with the single word OK."},
                    {"role": "user", "content": "Connection test"},
                ],
                max_tokens=8,
            )
        except (RuntimeError, ValueError):
            pass
        return self.status()


ai_provider = AIProvider()