"""
CoinGecko Pro API toolkit for crypto market data.

Covers prices, markets, coin details, charts, search, trending,
global stats, and top gainers/losers. Read-only — no persistence.

Requires COINGECKO_API_KEY in the environment (Pro API key).
"""

import json
import os
from datetime import datetime, timezone
from typing import Any, Optional
import requests
from agno.tools import Toolkit

BASE_URL = "https://pro-api.coingecko.com/api/v3"
DEFAULT_TIMEOUT = 30


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


class CoinGeckoToolkit(Toolkit):
    """
    Query CoinGecko Pro for crypto prices, market data, and trends.

    Use when asked about crypto prices, market caps, volumes, historical
    charts, trending coins, gainers/losers, global stats, or token lookups.
    """

    def __init__(
        self,
        api_key: Optional[str] = None,
        base_url: str = BASE_URL,
        timeout: int = DEFAULT_TIMEOUT,
        **kwargs,
    ):
        self.api_key = api_key or os.environ.get("COINGECKO_API_KEY", "").strip()
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout

        if not self.api_key:
            raise ValueError("COINGECKO_API_KEY is required")

        tools = [
            self.get_prices,
            self.get_markets,
            self.get_coin,
            self.get_market_chart,
            self.search_coins,
            self.get_trending,
            self.get_global_market,
            self.get_top_gainers_losers,
            self.api_get,
        ]
        super().__init__(name="coingecko", tools=tools, **kwargs)

    def _request(
        self,
        path: str,
        params: Optional[dict[str, Any]] = None,
    ) -> dict:
        """Execute a GET against the CoinGecko Pro API."""
        path = path if path.startswith("/") else f"/{path}"
        url = f"{self.base_url}{path}"
        query = dict(params or {})
        query["x_cg_pro_api_key"] = self.api_key
        headers = {
            "Accept": "application/json",
            "x-cg-pro-api-key": self.api_key,
        }

        try:
            response = requests.get(
                url,
                params=query,
                headers=headers,
                timeout=self.timeout,
            )
            response.raise_for_status()
            data = response.json()
            return {
                "status": "ok",
                "fetched_at": _utc_now(),
                "path": path,
                "data": data,
            }
        except requests.exceptions.HTTPError as e:
            body = ""
            if e.response is not None:
                try:
                    body = e.response.text[:500]
                except Exception:
                    pass
            return {
                "status": "error",
                "fetched_at": _utc_now(),
                "path": path,
                "reason": f"HTTP {e.response.status_code if e.response else 'unknown'}: {body or str(e)}",
            }
        except requests.exceptions.RequestException as e:
            return {
                "status": "error",
                "fetched_at": _utc_now(),
                "path": path,
                "reason": f"Request failed: {e}",
            }
        except json.JSONDecodeError as e:
            return {
                "status": "error",
                "fetched_at": _utc_now(),
                "path": path,
                "reason": f"Invalid JSON response: {e}",
            }

    def get_prices(
        self,
        coin_ids: str,
        vs_currencies: str = "usd,aud",
    ) -> dict:
        """
        Quick price lookup for one or more coins by CoinGecko ID.

        Args:
            coin_ids: Comma-separated coin IDs (e.g. "bitcoin,ethereum,solana").
            vs_currencies: Comma-separated fiat/crypto codes (e.g. "usd,aud").
        """
        return self._request(
            "/simple/price",
            {
                "ids": coin_ids,
                "vs_currencies": vs_currencies,
                "include_market_cap": "true",
                "include_24hr_vol": "true",
                "include_24hr_change": "true",
            },
        )

    def get_markets(
        self,
        vs_currency: str = "aud",
        per_page: int = 10,
        page: int = 1,
        order: str = "market_cap_desc",
    ) -> dict:
        """
        Market overview: price, market cap, volume, and 24h change for top coins.

        Args:
            vs_currency: Quote currency (e.g. "usd", "aud").
            per_page: Number of results (max 250).
            page: Page number for pagination.
            order: Sort order (default market_cap_desc).
        """
        return self._request(
            "/coins/markets",
            {
                "vs_currency": vs_currency,
                "per_page": per_page,
                "page": page,
                "order": order,
                "sparkline": "false",
            },
        )

    def get_coin(self, coin_id: str) -> dict:
        """
        Full metadata and market data for a single coin.

        Args:
            coin_id: CoinGecko slug ID (e.g. "bitcoin", "solana").
        """
        return self._request(
            f"/coins/{coin_id}",
            {
                "localization": "false",
                "tickers": "false",
                "market_data": "true",
                "community_data": "false",
                "developer_data": "false",
            },
        )

    def get_market_chart(
        self,
        coin_id: str,
        vs_currency: str = "usd",
        days: str = "7",
    ) -> dict:
        """
        Historical price, market cap, and volume for charting.

        Args:
            coin_id: CoinGecko slug ID.
            vs_currency: Quote currency (e.g. "usd", "aud").
            days: Number of days or "max" for full history.
        """
        return self._request(
            f"/coins/{coin_id}/market_chart",
            {"vs_currency": vs_currency, "days": days},
        )

    def search_coins(self, query: str) -> dict:
        """
        Find coins by name or symbol; returns matching IDs for follow-up calls.

        Args:
            query: Search term (e.g. "solana", "btc").
        """
        return self._request("/search", {"query": query})

    def get_trending(self) -> dict:
        """Coins trending on CoinGecko right now."""
        return self._request("/search/trending")

    def get_global_market(self) -> dict:
        """Total crypto market cap, BTC dominance, and 24h volume."""
        return self._request("/global")

    def get_top_gainers_losers(
        self,
        vs_currency: str = "usd",
        duration: str = "24h",
        top_coins: str = "1000",
    ) -> dict:
        """
        Top gainers and losers over a time window (Pro endpoint).

        Args:
            vs_currency: Quote currency (e.g. "usd", "aud").
            duration: Window such as "1h", "24h", "7d", "30d".
            top_coins: Universe size — "300", "500", or "1000".
        """
        return self._request(
            "/coins/top_gainers_losers",
            {
                "vs_currency": vs_currency,
                "duration": duration,
                "top_coins": top_coins,
            },
        )

    def api_get(self, path: str, params: str = "") -> dict:
        """
        Call any CoinGecko Pro GET endpoint not covered by other tools.

        Args:
            path: API path starting with / (e.g. "/exchange_rates", "/coins/categories").
            params: Optional query string without leading ? (e.g. "vs_currency=usd&per_page=5").
        """
        extra: dict[str, Any] = {}
        if params.strip():
            for pair in params.split("&"):
                if "=" in pair:
                    key, value = pair.split("=", 1)
                    extra[key.strip()] = value.strip()
        return self._request(path, extra if extra else None)
