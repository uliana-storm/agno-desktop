"""
Selective tool result compression for Tony.

Only tool results exceeding MIN_CHARS_TO_COMPRESS are sent to the
compression model. Small results (append_file acks, short reads, etc.)
are passed through unchanged — marking them as compressed_content=content
so Agno's compress loop skips them entirely.

Count-based trigger is disabled. Compression fires only when total context
approaches compress_token_limit (token trigger), and even then only pays
the LLM cost for results that are actually large.
"""

from dataclasses import dataclass
from typing import TYPE_CHECKING, List, Optional

from agno.compression.manager import CompressionManager
from agno.models.message import Message

if TYPE_CHECKING:
    from agno.metrics import RunMetrics

# ~500 tokens at 4 chars/token — tune up or down as needed.
MIN_CHARS_TO_COMPRESS = 2_000


@dataclass
class SelectiveCompressionManager(CompressionManager):
    """CompressionManager that skips small tool results."""

    min_chars: int = MIN_CHARS_TO_COMPRESS

    def __post_init__(self) -> None:
        self.compress_tool_results_limit = None

    def _mark_small_tools(self, messages: List[Message]) -> None:
        for msg in messages:
            if msg.role != "tool" or msg.compressed_content is not None or msg.content is None:
                continue
            content_str = msg.content if isinstance(msg.content, str) else str(msg.content)
            if len(content_str) < self.min_chars:
                msg.compressed_content = content_str

    def compress(
        self,
        messages: List[Message],
        run_metrics: Optional["RunMetrics"] = None,
    ) -> None:
        self._mark_small_tools(messages)
        super().compress(messages, run_metrics=run_metrics)

    async def acompress(
        self,
        messages: List[Message],
        run_metrics: Optional["RunMetrics"] = None,
    ) -> None:
        self._mark_small_tools(messages)
        await super().acompress(messages, run_metrics=run_metrics)
