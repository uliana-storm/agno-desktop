"""
CoinGecko Pro API toolkit for crypto market data.

All methods return plain text (CSV or labeled blocks) — never raw JSON.
Full API responses are processed by deterministic formatters before
entering agent context. No truncation, no LLM compression needed.

Return format:
  - Single coin:   labeled block  "=== bitcoin ===\nfield,value\n..."
  - Multi-coin:    one block per coin, separated by blank line
  - Time series:   resampled CSV  "=== bitcoin — 7d USD ===\ndate,price,..."
  - List/aggregate: single CSV table

api_get (escape hatch) routes through Qwopus 4B extractor at port 8083.

Requires COINGECKO_API_KEY in environment (Pro API key).
"""

import io
import os
import csv
from datetime import datetime, timezone, timedelta
from typing import Any, Optional
import requests
from agno.tools import Toolkit

from tools.extractor_client import extract_relevant

BASE_URL = "https://pro-api.coingecko.com/api/v3"
DEFAULT_TIMEOUT = 30


# ---------------------------------------------------------------------------
# Utilities
# ---------------------------------------------------------------------------

def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _fmt_large(value: Any) -> str:
    """Format large numbers as T/B/M suffixes. Returns '-' on failure."""
    try:
        v = float(value)
    except (TypeError, ValueError):
        return "-"
    if v >= 1_000_000_000_000:
        return f"{v / 1_000_000_000_000:.2f}T"
    if v >= 1_000_000_000:
        return f"{v / 1_000_000_000:.2f}B"
    if v >= 1_000_000:
        return f"{v / 1_000_000:.2f}M"
    return f"{v:,.2f}"


def _fmt_pct(value: Any) -> str:
    """Format a percentage with sign. Returns '-' on failure."""
    try:
        v = float(value)
        return f"{v:+.2f}%"
    except (TypeError, ValueError):
        return "-"


def _fmt_price(value: Any) -> str:
    """Format a price with appropriate decimal places."""
    try:
        v = float(value)
        if v >= 1000:
            return f"{v:,.2f}"
        if v >= 1:
            return f"{v:.4f}"
        return f"{v:.8f}"
    except (TypeError, ValueError):
        return "-"


def _to_csv_string(rows: list[dict], fieldnames: list[str]) -> str:
    """Render a list of dicts as a CSV string."""
    buf = io.StringIO()
    writer = csv.DictWriter(buf, fieldnames=fieldnames, extrasaction="ignore", lineterminator="\n")
    writer.writeheader()
    writer.writerows(rows)
    return buf.getvalue()


def _coin_block(coin_id: str, rows: list[tuple[str, str]]) -> str:
    """
    Render a labeled key-value block for a single coin.
    rows: list of (field, value) tuples.
    """
    lines = [f"=== {coin_id} ===", "field,value"]
    for field, value in rows:
        lines.append(f"{field},{value}")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Formatters — deterministic, no LLM
# ---------------------------------------------------------------------------

def _fmt_prices(data: dict, coin_id: str, currencies: list[str]) -> str:
    """Format simple/price response for one coin into a labeled block."""
    coin_data = data.get(coin_id, {})
    rows = []
    for cur in currencies:
        price = _fmt_price(coin_data.get(cur))
        rows.append((f"price_{cur}", price))
    rows.append(("24h_pct", _fmt_pct(coin_data.get("usd_24h_change"))))
    rows.append(("mcap_usd", _fmt_large(coin_data.get("usd_market_cap"))))
    rows.append(("vol_24h_usd", _fmt_large(coin_data.get("usd_24h_vol"))))
    return _coin_block(coin_id, rows)


def _fmt_markets(data: list) -> str:
    """Format /coins/markets list into a ranked CSV table."""
    fieldnames = ["rank", "name", "symbol", "price_usd", "24h_pct", "mcap_usd", "vol_24h"]
    table_rows = []
    for coin in data:
        table_rows.append({
            "rank":      coin.get("market_cap_rank", "-"),
            "name":      coin.get("name", "-"),
            "symbol":    (coin.get("symbol") or "").upper(),
            "price_usd": _fmt_price(coin.get("current_price")),
            "24h_pct":   _fmt_pct(coin.get("price_change_percentage_24h")),
            "mcap_usd":  _fmt_large(coin.get("market_cap")),
            "vol_24h":   _fmt_large(coin.get("total_volume")),
        })
    return _to_csv_string(table_rows, fieldnames)


def _fmt_coin(data: dict) -> str:
    """Format /coins/{id} full detail into a labeled block."""
    name      = data.get("name", "unknown")
    symbol    = (data.get("symbol") or "").upper()
    md        = data.get("market_data", {})
    desc_raw  = (data.get("description", {}).get("en") or "")
    # First sentence of description only
    desc = desc_raw.split(". ")[0].strip() if desc_raw else "-"

    def p(key: str, cur: str = "usd") -> str:
        val = md.get(key, {})
        return _fmt_price(val.get(cur)) if isinstance(val, dict) else "-"

    def pct(key: str) -> str:
        return _fmt_pct(md.get(key))

    rows = [
        ("name",           name),
        ("symbol",         symbol),
        ("rank",           str(data.get("market_cap_rank", "-"))),
        ("price_usd",      p("current_price")),
        ("price_aud",      p("current_price", "aud")),
        ("mcap_usd",       _fmt_large(md.get("market_cap", {}).get("usd"))),
        ("vol_24h_usd",    _fmt_large(md.get("total_volume", {}).get("usd"))),
        ("24h_pct",        pct("price_change_percentage_24h")),
        ("7d_pct",         pct("price_change_percentage_7d")),
        ("30d_pct",        pct("price_change_percentage_30d")),
        ("ath_usd",        p("ath")),
        ("ath_down_pct",   pct("ath_change_percentage")),
        ("circulating",    _fmt_large(md.get("circulating_supply"))),
        ("max_supply",     _fmt_large(md.get("max_supply"))),
        ("description",    desc),
    ]
    return _coin_block(data.get("id", name.lower()), rows)


def _resample_chart(
    prices: list,
    mcaps: list,
    volumes: list,
    days: str,
) -> list[dict]:
    """
    Resample CoinGecko OHLCV arrays into compact rows.

    Granularity rules:
      1–2 days   → hourly  (≤48 rows)
      3–90 days  → daily   (≤90 rows)
      91+ / max  → weekly  (≤53 rows)  monthly if > 365 days
    """
    # Pair timestamps with values
    price_map:  dict[str, list[float]] = {}
    mcap_map:   dict[str, list[float]] = {}
    vol_map:    dict[str, list[float]] = {}

    try:
        days_int = int(days)
    except (ValueError, TypeError):
        days_int = 9999  # "max"

    def bucket(ts_ms: int) -> str:
        dt = datetime.fromtimestamp(ts_ms / 1000, tz=timezone.utc)
        if days_int <= 2:
            return dt.strftime("%Y-%m-%d %H:00")
        if days_int <= 90:
            return dt.strftime("%Y-%m-%d")
        if days_int <= 365:
            # ISO week
            return dt.strftime("%Y-W%W")
        return dt.strftime("%Y-%m")

    for ts, val in prices:
        b = bucket(int(ts))
        price_map.setdefault(b, []).append(float(val))
    for ts, val in mcaps:
        b = bucket(int(ts))
        mcap_map.setdefault(b, []).append(float(val))
    for ts, val in volumes:
        b = bucket(int(ts))
        vol_map.setdefault(b, []).append(float(val))

    rows = []
    for period in sorted(price_map.keys()):
        plist = price_map[period]
        vlist = vol_map.get(period, [0])
        rows.append({
            "period":    period,
            "open":      _fmt_price(plist[0]),
            "close":     _fmt_price(plist[-1]),
            "high":      _fmt_price(max(plist)),
            "low":       _fmt_price(min(plist)),
            "chg_pct":   _fmt_pct((plist[-1] - plist[0]) / plist[0] * 100 if plist[0] else 0),
            "mcap":      _fmt_large(mcap_map.get(period, [None])[-1]),
            "vol":       _fmt_large(sum(vlist)),
        })
    return rows


def _fmt_chart(data: dict, coin_id: str, vs_currency: str, days: str) -> str:
    """Format market_chart response into resampled time series CSV."""
    prices  = data.get("prices", [])
    mcaps   = data.get("market_caps", [])
    volumes = data.get("total_volumes", [])

    if not prices:
        return f"=== {coin_id} — {days}d {vs_currency.upper()} ===\nNo data returned."

    rows = _resample_chart(prices, mcaps, volumes, days)
    header = f"=== {coin_id} — {days}d {vs_currency.upper()} ==="
    fieldnames = ["period", "open", "close", "high", "low", "chg_pct", "mcap", "vol"]
    return f"{header}\n{_to_csv_string(rows, fieldnames)}"


def _fmt_trending(data: dict) -> str:
    """Format trending coins into a compact CSV."""
    coins = data.get("coins", [])
    fieldnames = ["rank", "name", "symbol", "mcap_rank", "24h_pct"]
    rows = []
    for i, item in enumerate(coins[:10], 1):
        coin = item.get("item", {})
        rows.append({
            "rank":      i,
            "name":      coin.get("name", "-"),
            "symbol":    (coin.get("symbol") or "").upper(),
            "mcap_rank": coin.get("market_cap_rank", "-"),
            "24h_pct":   _fmt_pct(
                coin.get("data", {}).get("price_change_percentage_24h", {}).get("usd")
            ),
        })
    return _to_csv_string(rows, fieldnames)


def _fmt_gainers_losers(data: dict) -> str:
    """Format top gainers and losers into two CSV sections."""
    gainers = data.get("top_gainers", [])[:10]
    losers  = data.get("top_losers",  [])[:10]
    fieldnames = ["rank", "name", "symbol", "price_usd", "chg_pct"]

    def section(coins: list, label: str) -> str:
        rows = []
        for i, c in enumerate(coins, 1):
            rows.append({
                "rank":      i,
                "name":      c.get("name", "-"),
                "symbol":    (c.get("symbol") or "").upper(),
                "price_usd": _fmt_price(c.get("usd")),
                "chg_pct":   _fmt_pct(c.get("usd_24h_change")),
            })
        return f"=== {label} ===\n{_to_csv_string(rows, fieldnames)}"

    return f"{section(gainers, 'top gainers')}\n{section(losers, 'top losers')}"


def _fmt_global(data: dict) -> str:
    """Format global market stats into a single-row CSV."""
    gd = data.get("data", {})
    fieldnames = ["total_mcap_usd", "btc_dominance", "eth_dominance", "vol_24h_usd", "market_cap_change_24h_pct"]
    rows = [{
        "total_mcap_usd":           _fmt_large(gd.get("total_market_cap", {}).get("usd")),
        "btc_dominance":            _fmt_pct(gd.get("market_cap_percentage", {}).get("btc")),
        "eth_dominance":            _fmt_pct(gd.get("market_cap_percentage", {}).get("eth")),
        "vol_24h_usd":              _fmt_large(gd.get("total_volume", {}).get("usd")),
        "market_cap_change_24h_pct": _fmt_pct(gd.get("market_cap_change_percentage_24h_usd")),
    }]
    return _to_csv_string(rows, fieldnames)


# ---------------------------------------------------------------------------
# HTTP layer
# ---------------------------------------------------------------------------

class CoinGeckoToolkit(Toolkit):
    """
    Query CoinGecko Pro for crypto prices, market data, and trends.

    Use when asked about crypto prices, market caps, volumes, historical
    charts, trending coins, gainers/losers, global stats, or token lookups.

    All responses are plain text CSV or labeled blocks — never raw JSON.
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
        self.timeout  = timeout

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

    def _request(self, path: str, params: Optional[dict[str, Any]] = None) -> tuple[bool, Any]:
        """
        Execute GET against CoinGecko Pro.
        Returns (success: bool, data: dict | str).
        """
        path  = path if path.startswith("/") else f"/{path}"
        url   = f"{self.base_url}{path}"
        query = dict(params or {})
        query["x_cg_pro_api_key"] = self.api_key
        headers = {
            "Accept": "application/json",
            "x-cg-pro-api-key": self.api_key,
        }
        try:
            resp = requests.get(url, params=query, headers=headers, timeout=self.timeout)
            resp.raise_for_status()
            return True, resp.json()
        except requests.exceptions.HTTPError as e:
            body = ""
            if e.response is not None:
                try:
                    body = e.response.text[:300]
                except Exception:
                    pass
            return False, f"HTTP {e.response.status_code if e.response else '?'}: {body or str(e)}"
        except requests.exceptions.RequestException as e:
            return False, f"Request failed: {e}"

    # ------------------------------------------------------------------
    # Public tools
    # ------------------------------------------------------------------

    def get_prices(
        self,
        coin_ids: str,
        vs_currencies: str = "usd,aud",
    ) -> str:
        """
        Quick price lookup for one or more coins.
        Returns one labeled CSV block per coin.

        Args:
            coin_ids:      Comma-separated CoinGecko IDs (e.g. "bitcoin,ethereum,solana").
            vs_currencies: Comma-separated currency codes (e.g. "usd,aud").
        """
        ok, data = self._request(
            "/simple/price",
            {
                "ids":               coin_ids,
                "vs_currencies":     vs_currencies,
                "include_market_cap":  "true",
                "include_24hr_vol":    "true",
                "include_24hr_change": "true",
            },
        )
        if not ok:
            return f"Error fetching prices: {data}"

        currencies = [c.strip() for c in vs_currencies.split(",")]
        blocks = []
        for coin_id in [c.strip() for c in coin_ids.split(",")]:
            if coin_id not in data:
                blocks.append(f"=== {coin_id} ===\nNo data returned.")
            else:
                blocks.append(_fmt_prices(data, coin_id, currencies))
        return "\n\n".join(blocks)

    def get_markets(
        self,
        vs_currency: str = "usd",
        per_page: int = 10,
        page: int = 1,
        order: str = "market_cap_desc",
    ) -> str:
        """
        Ranked market overview: price, mcap, volume, 24h change.
        Returns a single CSV table (not per-coin — this is a list view).

        Args:
            vs_currency: Quote currency (e.g. "usd", "aud").
            per_page:    Number of coins (max 25).
            page:        Page number.
            order:       Sort order (default market_cap_desc).
        """
        per_page = min(max(int(per_page), 1), 25)
        ok, data = self._request(
            "/coins/markets",
            {
                "vs_currency": vs_currency,
                "per_page":    per_page,
                "page":        page,
                "order":       order,
                "sparkline":   "false",
            },
        )
        if not ok:
            return f"Error fetching markets: {data}"
        return _fmt_markets(data)

    def get_coin(self, coin_id: str) -> str:
        """
        Full metadata and market data for a single coin.
        Returns a labeled block with price, mcap, changes, ATH, and 1-line description.

        Args:
            coin_id: CoinGecko slug ID (e.g. "bitcoin", "solana").
        """
        ok, data = self._request(
            f"/coins/{coin_id}",
            {
                "localization":    "false",
                "tickers":         "false",
                "market_data":     "true",
                "community_data":  "false",
                "developer_data":  "false",
            },
        )
        if not ok:
            return f"Error fetching coin {coin_id}: {data}"
        return _fmt_coin(data)

    def get_market_chart(
        self,
        coin_id: str,
        vs_currency: str = "usd",
        days: str = "7",
    ) -> str:
        """
        Historical price chart for a single coin, resampled to a compact CSV.

        Granularity is automatically chosen based on the days range:
          1–2 days  → hourly rows
          3–90 days → daily rows
          91+ / max → weekly rows  (monthly if > 365 days)

        Always ≤90 rows regardless of range.

        Args:
            coin_id:     CoinGecko slug ID.
            vs_currency: Quote currency (e.g. "usd", "aud").
            days:        Number of days back, or "max" for full history.
        """
        ok, data = self._request(
            f"/coins/{coin_id}/market_chart",
            {"vs_currency": vs_currency, "days": days},
        )
        if not ok:
            return f"Error fetching chart for {coin_id}: {data}"
        return _fmt_chart(data, coin_id, vs_currency, days)

    def search_coins(self, query: str) -> str:
        """
        Find coins by name or symbol.
        Returns a CSV of matching coin IDs — use IDs with other tools.

        Args:
            query: Search term (e.g. "solana", "btc").
        """
        ok, data = self._request("/search", {"query": query})
        if not ok:
            return f"Error searching coins: {data}"

        coins = data.get("coins", [])[:10]
        if not coins:
            return f"No coins found matching '{query}'."

        fieldnames = ["id", "name", "symbol", "mcap_rank"]
        rows = [
            {
                "id":        c.get("id", "-"),
                "name":      c.get("name", "-"),
                "symbol":    (c.get("symbol") or "").upper(),
                "mcap_rank": c.get("market_cap_rank", "-"),
            }
            for c in coins
        ]
        return _to_csv_string(rows, fieldnames)

    def get_trending(self) -> str:
        """
        Coins trending on CoinGecko right now.
        Returns top 10 as a CSV with name, symbol, mcap rank, and 24h change.
        """
        ok, data = self._request("/search/trending")
        if not ok:
            return f"Error fetching trending: {data}"
        return _fmt_trending(data)

    def get_global_market(self) -> str:
        """
        Total crypto market cap, BTC dominance, ETH dominance, and 24h volume.
        Returns a single CSV row.
        """
        ok, data = self._request("/global")
        if not ok:
            return f"Error fetching global market: {data}"
        return _fmt_global(data)

    def get_top_gainers_losers(
        self,
        vs_currency: str = "usd",
        duration: str = "24h",
        top_coins: str = "100",
    ) -> str:
        """
        Top 10 gainers and top 10 losers over a time window (Pro endpoint).
        Returns two CSV sections.

        Args:
            vs_currency: Quote currency (e.g. "usd", "aud").
            duration:    Window — "1h", "24h", "7d", "30d".
            top_coins:   Universe size — "100" or "300".
        """
        ok, data = self._request(
            "/coins/top_gainers_losers",
            {
                "vs_currency": vs_currency,
                "duration":    duration,
                "top_coins":   top_coins,
            },
        )
        if not ok:
            return f"Error fetching gainers/losers: {data}"
        return _fmt_gainers_losers(data)

    def api_get(self, path: str, params: str = "", research_question: str = "") -> str:
        """
        Call any CoinGecko Pro GET endpoint not covered by other tools.
        Response is compressed via Qwopus 4B extractor (port 8083).
        Always provide research_question so the extract is focused.

        Args:
            path:              API path starting with / (e.g. "/exchange_rates").
            params:            Query string without leading ? (e.g. "vs_currency=usd").
            research_question: What you are trying to find out — shapes the extract.
        """
        extra: dict[str, Any] = {}
        if params.strip():
            for pair in params.split("&"):
                if "=" in pair:
                    k, v = pair.split("=", 1)
                    extra[k.strip()] = v.strip()

        ok, data = self._request(path, extra or None)
        if not ok:
            return f"Error calling {path}: {data}"

        import json
        raw_text = json.dumps(data, default=str)

        if not research_question.strip():
            # No question — return a hard-capped preview rather than nothing
            return f"(no research_question provided — raw preview)\n{raw_text[:2000]}"

        result = extract_relevant(
            article_text=raw_text,
            research_question=research_question,
            max_words=400,
        )
        if result["status"] == "error":
            return f"Extractor failed for {path}: {result['error']}\nRaw preview:\n{raw_text[:1000]}"

        return f"Extract from {path}:\n{result['extract']}"