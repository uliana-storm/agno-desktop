"""
feed_fetch_tool.py

Fetches and parses RSS feeds from approved sources.
Includes full article text extraction via newspaper3k.
"""

import feedparser
import httpx
from datetime import datetime, timezone
from typing import Optional
from agno.tools import Toolkit


# --- Source registry ---

APPROVED_FEEDS: dict[str, str] = {
    "fintech_news_au":       "https://fintechnews.au/feed/",
    "coindesk":              "https://www.coindesk.com/arc/outboundfeeds/rss/?outputType=xml",
    "the_block":             "https://www.theblock.co/rss.xml",
    "cointelegraph_reg":     "https://cointelegraph.com/rss/tag/regulation",
    "fintech_news_ch":       "https://fintechnews.ch/feed/",
    "decrypt":               "https://decrypt.co/feed",
    "reuters_crypto":        "https://news.google.com/rss/search?q=site:reuters.com+cryptocurrency",
}


# --- HTTP Client with proper headers ---

DEFAULT_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Accept": "application/rss+xml,application/xml,application/xhtml+xml,text/html;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.5",
}


def _get_http_client() -> httpx.Client:
    """Return configured HTTP client with headers to avoid 403 errors."""
    return httpx.Client(
        headers=DEFAULT_HEADERS,
        timeout=15,
        follow_redirects=True,
    )


# --- Data shape ---

class FeedItem:
    """Normalised representation of a single feed entry."""

    def __init__(
        self,
        source: str,
        title: str,
        url: str,
        published: Optional[str],
        summary: Optional[str],
        full_text: Optional[str] = None,
    ):
        self.source = source
        self.title = title
        self.url = url
        self.published = published
        self.summary = summary
        self.full_text = full_text

    def to_dict(self) -> dict:
        return {
            "source":    self.source,
            "title":     self.title,
            "url":       self.url,
            "published": self.published,
            "summary":   self.summary,
            "full_text": self.full_text,
        }


# --- Parser ---

def _parse_published(entry: feedparser.FeedParserDict) -> Optional[str]:
    """Return ISO 8601 string from whichever date field is present."""
    if hasattr(entry, "published_parsed") and entry.published_parsed:
        try:
            dt = datetime(*entry.published_parsed[:6], tzinfo=timezone.utc)
            return dt.isoformat()
        except Exception:
            pass
    if hasattr(entry, "updated_parsed") and entry.updated_parsed:
        try:
            dt = datetime(*entry.updated_parsed[:6], tzinfo=timezone.utc)
            return dt.isoformat()
        except Exception:
            pass
    return None


def _parse_feed(source_key: str, url: str, max_items: int) -> list[dict]:
    """
    Fetch and parse a single RSS feed.
    Returns a list of normalised item dicts, or an error dict on failure.
    """
    try:
        with _get_http_client() as client:
            response = client.get(url)
            response.raise_for_status()
            feed = feedparser.parse(response.text)
    except httpx.HTTPStatusError as e:
        return [{"error": f"{source_key}: HTTP {e.response.status_code}", "url": url}]
    except httpx.RequestError as e:
        return [{"error": f"{source_key}: Request failed — {str(e)}", "url": url}]
    except Exception as e:
        return [{"error": f"{source_key}: Parse failed — {str(e)}", "url": url}]

    items = []
    for entry in feed.entries[:max_items]:
        # RSS feeds can use 'summary' or 'description' for the excerpt
        excerpt = getattr(entry, "summary", None) or getattr(entry, "description", None) or ""
        item = FeedItem(
            source=source_key,
            title=getattr(entry, "title", "").strip(),
            url=getattr(entry, "link", ""),
            published=_parse_published(entry),
            summary=excerpt.strip() or None,
        )
        items.append(item.to_dict())

    return items


def _extract_full_text(url: str) -> dict:
    """
    Fetch and extract full article text from a URL.
    Uses multiple fallback methods: newspaper3k, trafilatura, or raw HTML.
    """
    result = {
        "url": url,
        "full_text": None,
        "extraction_method": None,
        "error": None,
        "char_count": 0,
    }

    try:
        with _get_http_client() as client:
            response = client.get(url, timeout=20)
            response.raise_for_status()
            html = response.text
    except Exception as e:
        result["error"] = f"Failed to fetch article: {str(e)}"
        return result

    # Try newspaper3k first
    try:
        from newspaper import Article
        article = Article(url)
        article.set_html(html)
        article.parse()
        if article.text and len(article.text) > 100:
            result["full_text"] = article.text
            result["extraction_method"] = "newspaper3k"
            result["char_count"] = len(article.text)
            result["title"] = article.title
            return result
    except Exception:
        pass

    # Fallback to trafilatura
    try:
        import trafilatura
        text = trafilatura.extract(html, include_comments=False, include_tables=False)
        if text and len(text) > 100:
            result["full_text"] = text
            result["extraction_method"] = "trafilatura"
            result["char_count"] = len(text)
            return result
    except Exception:
        pass

    # Last resort: basic HTML text extraction
    try:
        from bs4 import BeautifulSoup
        soup = BeautifulSoup(html, 'html.parser')
        # Remove script and style elements
        for script in soup(["script", "style", "nav", "header", "footer", "aside"]):
            script.decompose()
        text = soup.get_text(separator='\n', strip=True)
        # Clean up whitespace
        lines = [line.strip() for line in text.split('\n') if line.strip()]
        text = '\n'.join(lines)
        if len(text) > 100:
            result["full_text"] = text
            result["extraction_method"] = "beautifulsoup"
            result["char_count"] = len(text)
            return result
    except Exception as e:
        result["error"] = f"All extraction methods failed: {str(e)}"
        return result

    result["error"] = "Could not extract meaningful text from article"
    return result


# --- Agno Toolkit ---

class NewsFeedToolkit(Toolkit):
    """
    Fetches the latest items from approved crypto/fintech news RSS feeds.
    Can also extract full article text from URLs.

    Design rules:
    - Read-only. No writes, no persistence.
    - Structured failure on fetch errors — never raises to the agent.
    - max_items_per_feed caps volume; increase only for archive queries.
    """

    def __init__(self, max_items_per_feed: int = 10, **kwargs):
        self.max_items_per_feed = max_items_per_feed
        tools = [
            self.fetch_all_feeds,
            self.fetch_feed_by_source,
            self.get_full_article_text,
        ]
        super().__init__(
            name="news_feed",
            tools=tools,
            **kwargs,
        )

    def fetch_all_feeds(self) -> dict:
        """
        Use this tool only when the user asks for a general news briefing across all sources.
        If the user names a specific source, use fetch_feed_by_source instead.
        Returns a structured result containing items from each source and any fetch errors.
        """
        results = {}
        errors = []

        for source_key, url in APPROVED_FEEDS.items():
            items = _parse_feed(source_key, url, self.max_items_per_feed)
            # Separate clean items from error entries
            clean = [i for i in items if "error" not in i]
            failed = [i for i in items if "error" in i]
            if clean:
                results[source_key] = clean
            if failed:
                errors.extend(failed)

        return {
            "status":      "partial" if errors else "ok",
            "fetched_at":  datetime.now(timezone.utc).isoformat(),
            "source_count": len(results),
            "item_count":  sum(len(v) for v in results.values()),
            "results":     results,
            "errors":      errors,
        }

    def fetch_feed_by_source(self, source_key: str) -> dict:
        """
        Always use this tool when a broker or agent requests news from a specific source.
        Use the source key exactly as listed: fintech_news_au, coindesk, the_block,
        cointelegraph_reg, fintech_news_ch, decrypt, reuters_crypto.
        Never call this with a URL — use the source key only.
        Returns article metadata including URLs (but not full text).
        """
        if source_key not in APPROVED_FEEDS:
            return {
                "status": "error",
                "reason": f"Unknown source key: '{source_key}'. "
                          f"Valid keys: {list(APPROVED_FEEDS.keys())}",
            }

        url = APPROVED_FEEDS[source_key]
        items = _parse_feed(source_key, url, self.max_items_per_feed)
        clean = [i for i in items if "error" not in i]
        failed = [i for i in items if "error" in i]

        if failed and not clean:
            return {
                "status": "error",
                "reason": failed[0]["error"],
                "source": source_key,
            }

        return {
            "status":     "partial" if failed else "ok",
            "fetched_at": datetime.now(timezone.utc).isoformat(),
            "source":     source_key,
            "item_count": len(clean),
            "items":      clean,
            "errors":     failed,
        }

    def get_full_article_text(self, url: str) -> dict:
        """
        Fetch and extract the FULL text content of an article from its URL.
        Use this AFTER getting article URLs from fetch_feed_by_source or fetch_all_feeds.
        Returns the complete article text, character count, and extraction method used.

        Args:
            url: The full article URL to extract text from
        """
        result = _extract_full_text(url)

        return {
            "status": "ok" if result["full_text"] else "error",
            "fetched_at": datetime.now(timezone.utc).isoformat(),
            "url": result["url"],
            "char_count": result["char_count"],
            "extraction_method": result["extraction_method"],
            "error": result["error"],
            "full_text_preview": result["full_text"][:500] + "..." if result["full_text"] else None,
            "full_text": result["full_text"],  # Complete text for agent use
        }
