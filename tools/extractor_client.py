"""
extractor_client.py

Stateless extraction client for the Negentropy 4B model on port 8083.
Sends article text + research question, returns a focused extract.

Reusable beyond news feeds — any tool that needs to compress raw text
before it enters the main model context can use this.

Config (in .env):
    MODEL_EXTRACTOR_URL=http://localhost:8083/v1/chat/completions
    MODEL_EXTRACTOR_ID=negentropy-4b   # must match llama.cpp --alias
"""

import os
import requests
from typing import Optional

# --- Config ---

EXTRACTOR_URL = os.environ.get(
    "MODEL_EXTRACTOR_URL",
    "http://localhost:8083/v1/chat/completions",
)
EXTRACTOR_MODEL = os.environ.get("MODEL_EXTRACTOR_ID", "negentropy-4b")

SYSTEM_PROMPT = (
    "You are a precise extraction assistant. "
    "Given an article and a research question, extract only the content "
    "that is directly relevant to the question. "
    "Be concise. Do not add commentary, opinions, or information not present in the article. "
    "Output plain prose. Max 400 words."
)

# Hard cap on article text passed to the extractor.
# ~8000 chars ≈ 2000 tokens — well within 200k context, avoids padding costs.
ARTICLE_CHAR_LIMIT = 8_000

# Timeout for the extractor call (seconds).
# Small model on local hardware — 30s is generous.
EXTRACTOR_TIMEOUT = 30


# --- Client ---

def extract_relevant(
    article_text: str,
    research_question: str,
    max_words: int = 400,
    timeout: int = EXTRACTOR_TIMEOUT,
) -> dict:
    """
    Send article text to the Negentropy extractor model and return a focused extract.

    Args:
        article_text:       Raw article text from newspaper3k/trafilatura.
        research_question:  The question or topic Tony is researching.
        max_words:          Soft cap passed in the prompt (default 400).
        timeout:            Request timeout in seconds.

    Returns:
        {
            "status":   "ok" | "error",
            "extract":  str | None,    # the extracted text
            "error":    str | None,    # error message if status == "error"
        }
    """
    if not article_text or not article_text.strip():
        return {"status": "error", "extract": None, "error": "Empty article text."}

    if not research_question or not research_question.strip():
        return {"status": "error", "extract": None, "error": "Empty research question."}

    # Truncate article before it hits the model
    truncated = article_text[:ARTICLE_CHAR_LIMIT]

    user_message = (
        f"Research question: {research_question}\n\n"
        f"Article:\n{truncated}"
    )

    payload = {
        "model": EXTRACTOR_MODEL,
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT.replace("400", str(max_words))},
            {"role": "user",   "content": user_message},
        ],
        "max_tokens": max_words * 2,   # tokens ≈ 2× words, safe headroom
        "temperature": 0.1,            # near-deterministic for extraction
        "stream": False,
    }

    try:
        response = requests.post(
            EXTRACTOR_URL,
            json=payload,
            timeout=timeout,
            headers={"Content-Type": "application/json"},
        )
        response.raise_for_status()
        data = response.json()
        extract = data["choices"][0]["message"]["content"].strip()
        return {"status": "ok", "extract": extract, "error": None}

    except requests.exceptions.Timeout:
        return {"status": "error", "extract": None, "error": "Extractor timed out."}
    except requests.exceptions.ConnectionError:
        return {
            "status": "error",
            "extract": None,
            "error": f"Could not connect to extractor at {EXTRACTOR_URL}. Is the model running on port 8083?",
        }
    except requests.exceptions.HTTPError as e:
        return {"status": "error", "extract": None, "error": f"HTTP error: {e}"}
    except (KeyError, IndexError) as e:
        return {"status": "error", "extract": None, "error": f"Unexpected response shape: {e}"}
    except Exception as e:
        return {"status": "error", "extract": None, "error": f"Unexpected error: {e}"}


def extractor_available(timeout: int = 5) -> bool:
    """
    Quick health check — returns True if the extractor is reachable.
    Use in startup scripts or verify_agent_os.py.
    """
    try:
        base_url = EXTRACTOR_URL.replace("/v1/chat/completions", "/health")
        response = requests.get(base_url, timeout=timeout)
        return response.status_code == 200
    except Exception:
        return False
