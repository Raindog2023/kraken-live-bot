from __future__ import annotations

import json
from decimal import Decimal, InvalidOperation
from typing import Any, Literal

import httpx
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from .config import settings


class Godmod3Error(RuntimeError):
    """Raised when market analysis cannot return a valid decision."""


class Godmod3Analysis(BaseModel):
    model_config = ConfigDict(extra="forbid")

    product_id: str = Field(min_length=1)
    action: Literal["BUY", "SELL", "HOLD"]
    confidence: int = Field(ge=0, le=100)
    rationale: str = Field(min_length=1)


def extract_json_object(content: Any) -> Any:
    if isinstance(content, dict):
        return content

    if isinstance(content, list):
        parts: list[str] = []
        for item in content:
            if isinstance(item, str):
                parts.append(item)
            elif isinstance(item, dict):
                text = item.get("text") or item.get("content")
                if isinstance(text, str):
                    parts.append(text)
        content = "\n".join(parts)

    if not isinstance(content, str):
        raise TypeError("Analysis content is not a string")

    text = content.strip()
    if text.startswith("```"):
        lines = text.splitlines()
        if lines and lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        text = "\n".join(lines).strip()

    try:
        return json.loads(text)
    except json.JSONDecodeError:
        start = text.find("{")
        end = text.rfind("}")
        if start != -1 and end > start:
            return json.loads(text[start : end + 1])
        raise


def to_decimal(value: Any) -> Decimal | None:
    try:
        if value is None:
            return None
        return Decimal(str(value))
    except (InvalidOperation, ValueError, TypeError):
        return None


def percent_change(
    old_value: Decimal | None,
    new_value: Decimal | None,
) -> float | None:
    if old_value is None or new_value is None:
        return None
    if old_value == 0:
        return None
    return float(((new_value - old_value) / old_value) * Decimal("100"))


def normalize_http_url(url: str, default: str) -> str:
    value = (url or "").strip().rstrip("/")
    if not value:
        return default.rstrip("/")
    if value.startswith("//"):
        value = "https:" + value
    if "://" not in value:
        value = "https://" + value
    return value.rstrip("/")


def local_momentum_analysis(
    product_id: str,
    market_data: dict[str, Any],
) -> Godmod3Analysis:
    product = market_data.get("product")
    if not isinstance(product, dict):
        product = market_data if isinstance(market_data, dict) else {}
    candles = market_data.get("candles", [])
    if not isinstance(candles, list):
        candles = []
    summary = summarize_candles(candles)
    change_5m = summary.get("change_5m_percent") or 0.0
    change_15m = summary.get("change_15m_percent") or 0.0
    change_1h = summary.get("change_1h_percent") or 0.0
    change_window = summary.get("change_full_window_percent") or 0.0
    try:
        change_24h = float(product.get("price_percentage_change_24h") or 0)
    except (TypeError, ValueError):
        change_24h = 0.0

    score = (
        (change_5m * 2.0)
        + (change_15m * 1.5)
        + change_1h
        + (change_window * 0.25)
        + (change_24h * 0.35)
    )
    if score >= 0.02:
        action = "BUY"
        confidence = min(90, 68 + int(abs(score) * 20))
        rationale = (
            "Local momentum fallback BUY: "
            f"5m={change_5m:.4f}%, 15m={change_15m:.4f}%, "
            f"1h={change_1h:.4f}%, 24h={change_24h:.4f}%."
        )
    elif score <= -0.02:
        action = "SELL"
        confidence = min(90, 68 + int(abs(score) * 20))
        rationale = (
            "Local momentum fallback SELL: "
            f"5m={change_5m:.4f}%, 15m={change_15m:.4f}%, "
            f"1h={change_1h:.4f}%, 24h={change_24h:.4f}%."
        )
    else:
        action = "HOLD"
        confidence = 55
        rationale = (
            "Local momentum fallback HOLD: no usable directional bias "
            f"(score={score:.4f})."
        )
    return Godmod3Analysis(
        product_id=product_id,
        action=action,
        confidence=confidence,
        rationale=rationale,
    )


def summarize_candles(candles: list[dict[str, Any]]) -> dict[str, Any]:
    if not candles:
        return {
            "candle_count": 0,
            "message": "No candle history available",
        }

    closes: list[Decimal] = []
    highs: list[Decimal] = []
    lows: list[Decimal] = []
    volumes: list[Decimal] = []

    for candle in candles:
        close = to_decimal(candle.get("close"))
        high = to_decimal(candle.get("high"))
        low = to_decimal(candle.get("low"))
        volume = to_decimal(candle.get("volume"))
        if close is not None:
            closes.append(close)
        if high is not None:
            highs.append(high)
        if low is not None:
            lows.append(low)
        if volume is not None:
            volumes.append(volume)

    if not closes:
        return {
            "candle_count": len(candles),
            "message": "Candle history contained no valid closes",
        }

    first_close = closes[0]
    latest_close = closes[-1]
    change_5m = percent_change(closes[-2], closes[-1]) if len(closes) >= 2 else None
    change_15m = percent_change(closes[-4], closes[-1]) if len(closes) >= 4 else None
    change_30m = percent_change(closes[-7], closes[-1]) if len(closes) >= 7 else None
    change_1h = percent_change(closes[-13], closes[-1]) if len(closes) >= 13 else None

    highest_price = max(highs) if highs else None
    lowest_price = min(lows) if lows else None
    average_volume = None
    latest_volume = None
    latest_volume_vs_average_percent = None

    if volumes:
        average_volume = sum(volumes) / Decimal(len(volumes))
        latest_volume = volumes[-1]
        if average_volume > 0:
            latest_volume_vs_average_percent = float(
                ((latest_volume - average_volume) / average_volume)
                * Decimal("100")
            )

    return {
        "candle_count": len(candles),
        "first_close": str(first_close),
        "latest_close": str(latest_close),
        "highest_price": str(highest_price) if highest_price is not None else None,
        "lowest_price": str(lowest_price) if lowest_price is not None else None,
        "change_5m_percent": change_5m,
        "change_15m_percent": change_15m,
        "change_30m_percent": change_30m,
        "change_1h_percent": change_1h,
        "change_full_window_percent": percent_change(first_close, latest_close),
        "average_volume": str(average_volume) if average_volume is not None else None,
        "latest_volume": str(latest_volume) if latest_volume is not None else None,
        "latest_volume_vs_average_percent": latest_volume_vs_average_percent,
        "recent_closes": [str(value) for value in closes[-12:]],
    }


SYSTEM_PROMPT = (
    "You are a live cryptocurrency trading signal engine. "
    "You analyze only and never execute orders. "
    "Base your decision ONLY on the market data supplied in the user message. "
    "Never invent news, indicators, prices, volume, technical signals, or "
    "market conditions that were not provided. "
    "Prefer BUY or SELL whenever short-term price has a directional bias. "
    "Use BUY for positive 5m/15m/1h or 24h momentum. "
    "Use SELL for negative 5m/15m/1h or 24h momentum. "
    "Use HOLD only when every supplied change is essentially flat. "
    "Confidence represents how strongly the supplied market data supports "
    "the chosen action. Return exactly one JSON object containing "
    "product_id, action, confidence, and rationale. "
    "action must be BUY, SELL, or HOLD. "
    "confidence must be an integer from 0 to 100."
)


class MarketAnalyzer:
    def __init__(self) -> None:
        self.timeout = settings.request_timeout_seconds
        self.last_provider: str | None = None

    @property
    def configured(self) -> bool:
        return bool(self.available_providers())

    def available_providers(self) -> list[str]:
        providers: list[str] = []
        if getattr(settings, "anthropic_api_key", ""):
            providers.append("claude")
        if getattr(settings, "openai_api_key", ""):
            providers.append("openai")
        if getattr(settings, "gemini_api_key", ""):
            providers.append("gemini")
        if getattr(settings, "perplexity_api_key", ""):
            providers.append("perplexity")
        if getattr(settings, "godmode_api_key", "") or getattr(
            settings, "openrouter_api_key", ""
        ):
            providers.append("openrouter")
        return providers

    def _snapshot(
        self,
        product_id: str,
        market_data: dict[str, Any],
    ) -> dict[str, Any]:
        product = market_data.get("product")
        if not isinstance(product, dict):
            product = market_data
        candles = market_data.get("candles", [])
        if not isinstance(candles, list):
            candles = []
        return {
            "product_id": product_id,
            "current_market": {
                "price": product.get("price"),
                "price_percentage_change_24h": product.get(
                    "price_percentage_change_24h"
                ),
                "volume_24h": product.get("volume_24h"),
                "status": product.get("status"),
            },
            "recent_market_summary": summarize_candles(candles),
            "candle_granularity": market_data.get(
                "candle_granularity",
                "FIVE_MINUTE",
            ),
            "recent_candles": candles[-60:],
        }

    async def _post_json(
        self,
        url: str,
        headers: dict[str, str],
        payload: dict[str, Any],
    ) -> dict[str, Any]:
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            response = await client.post(url, headers=headers, json=payload)
        if response.status_code >= 400:
            raise Godmod3Error(
                f"HTTP {response.status_code}: {response.text[:500]}"
            )
        data = response.json()
        if not isinstance(data, dict):
            raise Godmod3Error("Provider returned a non-object response")
        return data

    def _parse_analysis(
        self,
        product_id: str,
        content: Any,
    ) -> Godmod3Analysis:
        raw = extract_json_object(content)
        analysis = Godmod3Analysis.model_validate(raw)
        if analysis.product_id != product_id:
            analysis = analysis.model_copy(update={"product_id": product_id})
        return analysis

    async def _ask_claude(
        self,
        system_prompt: str,
        user_prompt: str,
    ) -> str:
        model = getattr(settings, "anthropic_model", "") or "claude-sonnet-4-5"
        base_url = (
            getattr(settings, "anthropic_base_url", "")
            or "https://api.anthropic.com"
        ).rstrip("/")
        data = await self._post_json(
            f"{base_url}/v1/messages",
            {
                "x-api-key": settings.anthropic_api_key,
                "anthropic-version": "2023-06-01",
                "content-type": "application/json",
            },
            {
                "model": model,
                "max_tokens": 512,
                "temperature": 0,
                "system": system_prompt,
                "messages": [{"role": "user", "content": user_prompt}],
            },
        )
        pieces: list[str] = []
        for content in data.get("content", []):
            if content.get("type") == "text":
                text = content.get("text")
                if isinstance(text, str) and text.strip():
                    pieces.append(text.strip())
        if not pieces:
            raise Godmod3Error(f"Unexpected Claude response: {data}")
        return "\n".join(pieces)

    async def _ask_openai(
        self,
        system_prompt: str,
        user_prompt: str,
    ) -> str:
        model = getattr(settings, "openai_model", "") or "gpt-4.1-mini"
        base_url = (
            getattr(settings, "openai_base_url", "")
            or "https://api.openai.com/v1"
        ).rstrip("/")
        data = await self._post_json(
            f"{base_url}/chat/completions",
            {
                "Authorization": f"Bearer {settings.openai_api_key}",
                "Content-Type": "application/json",
            },
            {
                "model": model,
                "temperature": 0,
                "response_format": {"type": "json_object"},
                "messages": [
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt},
                ],
            },
        )
        content = data["choices"][0]["message"]["content"]
        if not content:
            raise Godmod3Error(f"Unexpected OpenAI response: {data}")
        return content

    async def _ask_gemini(
        self,
        system_prompt: str,
        user_prompt: str,
    ) -> str:
        model = getattr(settings, "gemini_model", "") or "gemini-2.0-flash"
        base_url = (
            getattr(settings, "gemini_base_url", "")
            or "https://generativelanguage.googleapis.com"
        ).rstrip("/")
        url = f"{base_url}/v1beta/models/{model}:generateContent"
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            response = await client.post(
                url,
                params={"key": settings.gemini_api_key},
                json={
                    "systemInstruction": {
                        "parts": [{"text": system_prompt}],
                    },
                    "contents": [
                        {
                            "role": "user",
                            "parts": [{"text": user_prompt}],
                        }
                    ],
                    "generationConfig": {
                        "temperature": 0,
                        "responseMimeType": "application/json",
                    },
                },
            )
        if response.status_code >= 400:
            raise Godmod3Error(
                f"Gemini HTTP {response.status_code}: {response.text[:500]}"
            )
        data = response.json()
        for candidate in data.get("candidates", []):
            for part in candidate.get("content", {}).get("parts", []):
                text = part.get("text")
                if isinstance(text, str) and text.strip():
                    return text.strip()
        raise Godmod3Error(f"Unexpected Gemini response: {data}")

    async def _ask_perplexity(
        self,
        system_prompt: str,
        user_prompt: str,
    ) -> str:
        model = (
            getattr(settings, "perplexity_model", "")
            or "sonar"
        )
        base_url = (
            getattr(settings, "perplexity_base_url", "")
            or "https://api.perplexity.ai"
        ).rstrip("/")
        data = await self._post_json(
            f"{base_url}/chat/completions",
            {
                "Authorization": f"Bearer {settings.perplexity_api_key}",
                "Content-Type": "application/json",
            },
            {
                "model": model,
                "temperature": 0,
                "messages": [
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt},
                ],
            },
        )
        content = data["choices"][0]["message"]["content"]
        if not content:
            raise Godmod3Error(f"Unexpected Perplexity response: {data}")
        return content

    async def _ask_openrouter(
        self,
        system_prompt: str,
        user_prompt: str,
    ) -> str:
        api_key = (
            getattr(settings, "godmode_api_key", "")
            or getattr(settings, "openrouter_api_key", "")
        )
        base_url = normalize_http_url(
            getattr(settings, "godmode_base_url", "")
            or getattr(settings, "openrouter_base_url", ""),
            "https://openrouter.ai/api/v1",
        )
        model = (
            getattr(settings, "godmode_model", "")
            or getattr(settings, "openrouter_model", "")
            or "openai/gpt-4.1-mini"
        )
        data = await self._post_json(
            f"{base_url}/chat/completions",
            {
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
            },
            {
                "model": model,
                "temperature": 0,
                "messages": [
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt},
                ],
            },
        )
        content = data["choices"][0]["message"]["content"]
        if not content:
            raise Godmod3Error(f"Unexpected OpenRouter response: {data}")
        return content

    async def analyze(
        self,
        product_id: str,
        market_data: dict[str, Any],
    ) -> Godmod3Analysis:
        snapshot = self._snapshot(product_id, market_data)
        user_prompt = (
            "Analyze this live market data and return JSON only:\n"
            + json.dumps(snapshot, indent=2)
        )

        providers = [
            ("claude", self._ask_claude),
            ("openai", self._ask_openai),
            ("gemini", self._ask_gemini),
            ("perplexity", self._ask_perplexity),
            ("openrouter", self._ask_openrouter),
        ]
        available = set(self.available_providers())
        errors: list[str] = []

        for name, caller in providers:
            if name not in available:
                continue
            try:
                content = await caller(SYSTEM_PROMPT, user_prompt)
                analysis = self._parse_analysis(product_id, content)
                self.last_provider = name
                if analysis.action in {"BUY", "SELL"} and analysis.confidence >= 50:
                    return analysis
                fallback = local_momentum_analysis(product_id, market_data)
                if fallback.action != "HOLD":
                    self.last_provider = f"{name}+local_momentum"
                    fallback.rationale = (
                        f"LLM returned {analysis.action} ({analysis.confidence}). "
                        + fallback.rationale
                    )
                    return fallback
                return analysis
            except (
                Godmod3Error,
                httpx.HTTPError,
                json.JSONDecodeError,
                KeyError,
                IndexError,
                TypeError,
                ValidationError,
            ) as exc:
                errors.append(f"{name}: {exc}")

        if errors:
            try:
                self.last_provider = "local_momentum"
                analysis = local_momentum_analysis(product_id, market_data)
                analysis.rationale = (
                    analysis.rationale
                    + " LLM providers failed: "
                    + " | ".join(errors[:3])
                )
                return analysis
            except Exception:
                pass

        try:
            self.last_provider = "local_momentum"
            return local_momentum_analysis(product_id, market_data)
        except Exception as exc:
            raise Godmod3Error(
                "All analysis providers failed: " + " | ".join(errors[:5] or [str(exc)])
            ) from exc


godmod3_client = MarketAnalyzer()
market_analyzer = godmod3_client
