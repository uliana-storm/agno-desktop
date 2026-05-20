"""
LLM Call Audit Logger for Agno

Logs every outbound LLM call with agent name, session ID, port, and token count.
Usage: Use LoggedOpenAILike instead of OpenAILike in agent factories.
"""

import json
from dataclasses import dataclass, field
from typing import Any, Dict, Iterator, List, Optional, Union, Type
from urllib.parse import urlparse

from agno.models.openai import OpenAILike
from agno.models.message import Message
from agno.models.response import ModelResponse
from agno.run.agent import RunOutput
from agno.run.team import TeamRunOutput
from agno.utils.log import log_info, log_debug


def estimate_token_count(text: str) -> int:
    """
    Rough token estimation for logging purposes.
    Uses a simple heuristic: ~4 characters per token for English text.
    This is not exact but sufficient for audit logging.
    """
    if not text:
        return 0
    # Rough estimate: 1 token ≈ 4 characters for English text
    return len(text) // 4


def extract_port_from_base_url(base_url: Optional[str]) -> str:
    """Extract port from base_url like 'http://localhost:8081/v1' -> '8081'."""
    if not base_url:
        return "unknown"
    try:
        parsed = urlparse(str(base_url))
        if parsed.port:
            return str(parsed.port)
        # Default ports for http/https
        return "80" if parsed.scheme == "http" else "443"
    except Exception:
        return "unknown"


def format_messages_for_token_estimate(messages: List[Message]) -> str:
    """Convert messages to a string for token estimation."""
    text_parts = []
    for msg in messages:
        if hasattr(msg, 'content') and msg.content:
            if isinstance(msg.content, str):
                text_parts.append(msg.content)
            elif isinstance(msg.content, list):
                for item in msg.content:
                    if isinstance(item, dict) and 'text' in item:
                        text_parts.append(item['text'])
        if hasattr(msg, 'tool_calls') and msg.tool_calls:
            text_parts.append(json.dumps(msg.tool_calls, default=str))
    return "\n".join(text_parts)


@dataclass
class LoggedOpenAILike(OpenAILike):
    """
    OpenAILike model wrapper that logs every LLM call with audit information.

    Additional attributes:
        agent_id: Identifier for the agent making the call (e.g., "jarvis", "tony")
        session_id: Session identifier for tracking
    """

    agent_id: str = "unknown"
    session_id: str = "unknown"

    def _log_llm_call(self, messages: List[Message], operation: str = "invoke") -> int:
        """
        Log the LLM call with agent, session, port, and token count.
        Returns the estimated token count.
        """
        import json as jsonlib
        port = extract_port_from_base_url(self.base_url)
        messages_text = format_messages_for_token_estimate(messages)
        token_count = estimate_token_count(messages_text)

        log_info(
            f"[llm_call] agent={self.agent_id} session={self.session_id} port={port} "
            f"operation={operation} tokens={token_count} model={self.id}"
        )

        # Print message breakdown to stdout for immediate visibility
        print(f"[llm_call:{self.agent_id}] BREAKDOWN: {len(messages)} messages, ~{token_count} tokens", flush=True)
        for i, msg in enumerate(messages):
            role = getattr(msg, 'role', '?')
            content = getattr(msg, 'content', '')
            if isinstance(content, str):
                chars = len(content)
            elif isinstance(content, list):
                chars = sum(len(str(item)) for item in content)
            else:
                chars = len(str(content))
            preview = (content[:60] if isinstance(content, str) else str(content)[:60]).replace('\n', ' ')
            print(f"  [{i}] {role}: ~{chars//4} tokens | {preview}...", flush=True)
            # System prompt full dump for diagnosis
            if role == "system" and isinstance(content, str):
                print(f"  [SYSTEM FULL]:\n{content}\n", flush=True)

        # Also log debug details for deep troubleshooting
        log_debug(
            f"[llm_call:detail] base_url={self.base_url} messages_count={len(messages)} "
            f"provider={self.provider}"
        )

        return token_count

    def invoke(
        self,
        messages: List[Message],
        assistant_message: Message,
        response_format: Optional[Union[Dict, Type[Any]]] = None,
        tools: Optional[List[Dict[str, Any]]] = None,
        tool_choice: Optional[Union[str, Dict[str, Any]]] = None,
        run_response: Optional[Union[RunOutput, TeamRunOutput]] = None,
        compress_tool_results: bool = False,
    ) -> ModelResponse:
        """Override invoke to add logging before the call."""
        self._log_llm_call(messages, operation="invoke")
        return super().invoke(
            messages=messages,
            assistant_message=assistant_message,
            response_format=response_format,
            tools=tools,
            tool_choice=tool_choice,
            run_response=run_response,
            compress_tool_results=compress_tool_results,
        )

    def ainvoke(
        self,
        messages: List[Message],
        assistant_message: Message,
        response_format: Optional[Union[Dict, Type[Any]]] = None,
        tools: Optional[List[Dict[str, Any]]] = None,
        tool_choice: Optional[Union[str, Dict[str, Any]]] = None,
        run_response: Optional[Union[RunOutput, TeamRunOutput]] = None,
        compress_tool_results: bool = False,
    ) -> Any:  # Returns a coroutine
        """Override ainvoke to add logging before the async call."""
        self._log_llm_call(messages, operation="ainvoke")
        return super().ainvoke(
            messages=messages,
            assistant_message=assistant_message,
            response_format=response_format,
            tools=tools,
            tool_choice=tool_choice,
            run_response=run_response,
            compress_tool_results=compress_tool_results,
        )

    def invoke_stream(
        self,
        messages: List[Message],
        assistant_message: Message,
        response_format: Optional[Union[Dict, Type[Any]]] = None,
        tools: Optional[List[Dict[str, Any]]] = None,
        tool_choice: Optional[Union[str, Dict[str, Any]]] = None,
        run_response: Optional[Union[RunOutput, TeamRunOutput]] = None,
        compress_tool_results: bool = False,
    ) -> Iterator[ModelResponse]:
        """Override invoke_stream to add logging before the streaming call."""
        self._log_llm_call(messages, operation="invoke_stream")
        yield from super().invoke_stream(
            messages=messages,
            assistant_message=assistant_message,
            response_format=response_format,
            tools=tools,
            tool_choice=tool_choice,
            run_response=run_response,
            compress_tool_results=compress_tool_results,
        )

    async def ainvoke_stream(
        self,
        messages: List[Message],
        assistant_message: Message,
        response_format: Optional[Union[Dict, Type[Any]]] = None,
        tools: Optional[List[Dict[str, Any]]] = None,
        tool_choice: Optional[Union[str, Dict[str, Any]]] = None,
        run_response: Optional[Union[RunOutput, TeamRunOutput]] = None,
        compress_tool_results: bool = False,
    ) -> Any:  # Returns async iterator
        """Override ainvoke_stream to add logging before the async streaming call."""
        self._log_llm_call(messages, operation="ainvoke_stream")
        return super().ainvoke_stream(
            messages=messages,
            assistant_message=assistant_message,
            response_format=response_format,
            tools=tools,
            tool_choice=tool_choice,
            run_response=run_response,
            compress_tool_results=compress_tool_results,
        )

    def to_dict(self) -> Dict[str, Any]:
        """Include logging metadata in dict representation."""
        model_dict = super().to_dict()
        model_dict.update({
            "agent_id": self.agent_id,
            "session_id": self.session_id,
            "logged_port": extract_port_from_base_url(self.base_url),
        })
        return model_dict
