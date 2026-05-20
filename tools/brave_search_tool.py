"""
Custom Brave Search tool that uses requests directly.
Avoids the segfault issues with the brave-search package.
"""

import os
from typing import List, Optional

import requests
from agno.tools.toolkit import Toolkit
from agno.tools.function import Function


class BraveSearchToolkit(Toolkit):
    """Brave Search API toolkit - custom implementation."""

    def __init__(
        self,
        api_key: Optional[str] = None,
        fixed_max_results: int = 5,
    ):
        super().__init__(name="brave_search")
        self.api_key = api_key or os.environ.get("BRAVE_API_KEY")
        self.fixed_max_results = fixed_max_results
        self.base_url = "https://api.search.brave.com/res/v1/web/search"

        if not self.api_key:
            raise ValueError("BRAVE_API_KEY is required")

        self.register(self.brave_search)

    def brave_search(self, query: str) -> str:
        """
        Search the web using Brave Search API.

        Args:
            query: The search query string

        Returns:
            Formatted search results as a string
        """
        headers = {
            "Accept": "application/json",
            "X-Subscription-Token": self.api_key,
        }

        params = {
            "q": query,
            "count": self.fixed_max_results,
            "offset": 0,
            "mkt": "en-US",
            "safesearch": "moderate",
            "freshness": "pd",  # past day
            "text_decorations": False,
        }

        try:
            response = requests.get(
                self.base_url,
                headers=headers,
                params=params,
                timeout=30
            )
            response.raise_for_status()
            data = response.json()

            results = data.get("web", {}).get("results", [])

            if not results:
                return "No results found for this query."

            formatted_results = []
            for i, result in enumerate(results[:self.fixed_max_results], 1):
                title = result.get("title", "No title")
                url = result.get("url", "")
                description = result.get("description", "No description")

                formatted_results.append(
                    f"{i}. {title}\n   URL: {url}\n   {description}\n"
                )

            return "\n".join(formatted_results)

        except requests.exceptions.RequestException as e:
            return f"Search error: {str(e)}"
        except Exception as e:
            return f"Unexpected error: {str(e)}"
