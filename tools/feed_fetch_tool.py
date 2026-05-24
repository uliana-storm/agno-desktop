"""
feed_fetch_tool.py

Fetches and parses RSS feeds from approved sources.
Three-stage design:
  1. list_sources()             → model picks relevant sources
  2. fetch_feed_by_source()     → model picks relevant articles
  3. fetch_and_extract()        → full text fetched + compressed via Qwopus 4B
                                  only the extract enters the main model context

Full article text never reaches Tony directly.
"""

import feedparser
import httpx
from datetime import datetime, timezone, timedelta
from typing import Optional
from agno.tools import Toolkit

from tools.extractor_client import extract_relevant


# ---------------------------------------------------------------------------
# Source registry
# Each entry: url + description so the model can select sources intelligently.
# ---------------------------------------------------------------------------

APPROVED_FEEDS: dict[str, dict] = {
    "fintech_news_au": {
        "url": "https://fintechnews.au/feed/",
        "description": (
            "Australian fintech industry news — startups, payments, lending, "
            "open banking, local regulatory developments."
        ),
    },
    "coindesk": {
        "url": "https://www.coindesk.com/arc/outboundfeeds/rss/?outputType=xml",
        "description": (
            "Breaking crypto and digital asset news — market moves, institutional "
            "activity, DeFi, NFTs, Bitcoin, Ethereum."
        ),
    },
    "the_block": {
        "url": "https://www.theblock.co/rss.xml",
        "description": (
            "In-depth crypto research and news — on-chain data, funding rounds, "
            "protocol analysis, exchange activity."
        ),
    },
    "cointelegraph_reg": {
        "url": "https://cointelegraph.com/rss/tag/regulation",
        "description": (
            "Crypto regulation only — government policy, SEC/CFTC/ASIC actions, "
            "legislation, enforcement, compliance."
        ),
    },
    "fintech_news_ch": {
        "url": "https://fintechnews.ch/feed/",
        "description": (
            "Swiss and European fintech — banking innovation, WealthTech, "
            "RegTech, crypto regulation in Europe."
        ),
    },
    "decrypt": {
        "url": "https://decrypt.co/feed",
        "description": (
            "Consumer-focused crypto and Web3 news — NFTs, gaming, AI x crypto, "
            "accessible market explainers."
        ),
    },
    "reuters_crypto": {
        "url": "https://news.google.com/rss/search?q=site:reuters.com+cryptocurrency",
        "description": (
            "Reuters cryptocurrency coverage — institutional, macroeconomic angle, "
            "high credibility, lower volume."
        ),
    },
}


# ---------------------------------------------------------------------------
# HTTP client
# ---------------------------------------------------------------------------

DEFAULT_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    ),
    "Accept": "application/rss+xml,application/xml,application/xhtml+xml,text/html;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.5",
}


def _get_http_client() -> httpx.Client:
    return httpx.Client(headers=DEFAULT_HEADERS, timeout=15, follow_redirects=True)


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _parse_published(entry: feedparser.FeedParserDict) -> Optional[datetime]:
    """Return timezone-aware datetime from whichever date field is present."""
    for attr in ("published_parsed", "updated_parsed"):
        val = getattr(entry, attr, None)
        if val:
            try:
                return datetime(*val[:6], tzinfo=timezone.utc)
            except Exception:
                pass
    return None


def _parse_feed(source_key: str, url: str, max_items: int, cutoff: Optional[datetime] = None) -> list[dict]:
    """
    Fetch and parse a single RSS feed.
    Filters by cutoff datetime if provided.
    Returns minimal item dicts: source, title, url, published.
    """
    try:
        with _get_http_client() as client:
            response = client.get(url)
            response.raise_for_status()
            feed = feedparser.parse(response.text)
    except httpx.HTTPStatusError as e:
        return [{"error": f"{source_key}: HTTP {e.response.status_code}"}]
    except httpx.RequestError as e:
        return [{"error": f"{source_key}: request failed — {e}"}]
    except Exception as e:
        return [{"error": f"{source_key}: parse failed — {e}"}]

    items = []
    for entry in feed.entries:
        if len(items) >= max_items:
            break

        published_dt = _parse_published(entry)

        # Apply time filter if requested
        if cutoff and published_dt and published_dt < cutoff:
            continue

        items.append({
            "source":    source_key,
            "title":     getattr(entry, "title", "").strip(),
            "url":       getattr(entry, "link", ""),
            "published": published_dt.isoformat() if published_dt else None,
        })

    return items


def _fetch_article_text(url: str) -> Optional[str]:
    """
    Fetch full article text. Tries newspaper3k → trafilatura → BeautifulSoup.
    Returns raw text or None on complete failure.
    Internal only — never returned to agent directly.
    """
    try:
        with _get_http_client() as client:
            response = client.get(url, timeout=20)
            response.raise_for_status()
            html = response.text
    except Exception:
        return None

    # newspaper3k
    try:
        from newspaper import Article
        article = Article(url)
        article.set_html(html)
        article.parse()
        if article.text and len(article.text) > 100:
            return article.text
    except Exception:
        pass

    # trafilatura
    try:
        import trafilatura
        text = trafilatura.extract(html, include_comments=False, include_tables=False)
        if text and len(text) > 100:
            return text
    except Exception:
        pass

    # BeautifulSoup fallback
    try:
        from bs4 import BeautifulSoup
        soup = BeautifulSoup(html, "html.parser")
        for tag in soup(["script", "style", "nav", "header", "footer", "aside"]):
            tag.decompose()
        lines = [l.strip() for l in soup.get_text(separator="\n").split("\n") if l.strip()]
        text = "\n".join(lines)
        if len(text) > 100:
            return text
    except Exception:
        pass

    return None


# ---------------------------------------------------------------------------
# Agno Toolkit
# ---------------------------------------------------------------------------

class NewsFeedToolkit(Toolkit):
    """
    Three-stage news research toolkit.

    Intended call order:
      1. list_sources()              — pick which sources are relevant to the task
      2. fetch_feed_by_source()      — get article headlines + URLs from chosen sources
      3. fetch_and_extract()         — fetch full article and extract relevant content
                                       via Qwopus 4B (port 8083); only the extract
                                       enters Tony's context

    Design rules:
    - All methods return plain strings, not dicts.
    - Full article text never enters Tony's context.
    - max_items_per_feed caps volume at fetch time.
    - hours filter applied server-side where possible, post-fetch otherwise.
    """

    def __init__(self, max_items_per_feed: int = 5, **kwargs):
        self.max_items_per_feed = max_items_per_feed
        tools = [
            self.list_sources,
            self.fetch_feed_by_source,
            self.fetch_and_extract,
        ]
        super().__init__(name="news_feed", tools=tools, **kwargs)

    # ------------------------------------------------------------------
    # Stage 1 — source selection
    # ------------------------------------------------------------------

    def list_sources(self) -> str:
        """
        List all available news sources with descriptions.
        Call this first to decide which sources are relevant to the research task.
        Returns a plain text list of source keys and what each covers.
        """
        lines = ["Available news sources:\n"]
        for key, meta in APPROVED_FEEDS.items():
            lines.append(f"{key}\n  {meta['description']}\n")
        return "\n".join(lines)

    # ------------------------------------------------------------------
    # Stage 2 — article listing
    # ------------------------------------------------------------------

    def fetch_feed_by_source(self, source_key: str, hours: int = 24) -> str:
        """
        Fetch recent article headlines and URLs from a single source.
        Always call list_sources first to confirm the source key.
        Returns a plain text list of articles — title, URL, published time.
        Use the URLs with fetch_and_extract to get article content.

        Args:
            source_key: Key from list_sources (e.g. 'coindesk', 'the_block').
            hours:      How many hours back to fetch. Default 24.
        """
        if source_key not in APPROVED_FEEDS:
            valid = ", ".join(APPROVED_FEEDS.keys())
            return f"Unknown source '{source_key}'. Valid keys: {valid}"

        url = APPROVED_FEEDS[source_key]["url"]
        cutoff = datetime.now(timezone.utc) - timedelta(hours=hours)

        # Fetch more than max_items so filtering doesn't leave us empty
        items = _parse_feed(source_key, url, max_items=self.max_items_per_feed * 3, cutoff=cutoff)

        errors = [i for i in items if "error" in i]
        clean  = [i for i in items if "error" not in i]

        if errors and not clean:
            return f"Failed to fetch {source_key}: {errors[0]['error']}"

        if not clean:
            return f"No articles found in {source_key} in the last {hours} hours."

        # Cap at max_items_per_feed after time filtering
        clean = clean[:self.max_items_per_feed]

        lines = [f"[{source_key}] — last {hours}h — {len(clean)} articles:\n"]
        for item in clean:
            pub = item["published"] or "unknown time"
            lines.append(f"• {item['title']}\n  {item['url']}\n  Published: {pub}\n")

        return "\n".join(lines)

    # ------------------------------------------------------------------
    # Stage 3 — full text extraction
    # ------------------------------------------------------------------

    def fetch_and_extract(self, url: str, research_question: str) -> str:
        """
        Fetch a full article and extract only what is relevant to the research question.
        Extraction is performed by Qwopus 4B (port 8083) — Tony never sees raw article text.
        Returns a focused 300-400 word extract, or an error message.

        Args:
            url:               Full article URL from fetch_feed_by_source.
            research_question: The specific question or topic you are researching.
                               Be specific — the extract is shaped by this question.
        """
        # Step 1: fetch raw text
        raw_text = _fetch_article_text(url)
        if not raw_text:
            return f"Could not extract text from {url}."

        # Step 2: compress via extractor model — never enters Tony's context
        result = extract_relevant(
            article_text=raw_text,
            research_question=research_question,
            max_words=400,
        )

        if result["status"] == "error":
            return f"Extraction failed for {url}: {result['error']}"

        char_count = len(raw_text)
        return (
            f"Extract from: {url}\n"
            f"(Article: {char_count:,} chars → compressed by extractor)\n\n"
            f"{result['extract']}"
        )