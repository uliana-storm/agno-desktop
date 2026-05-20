"""
Token Audit Utility for Agno

Analyzes prompt sizes to trace the origin of large token requests.
Run: .venv/bin/python scripts/token_audit.py
"""

import sys
from pathlib import Path

_root = Path(__file__).parent.parent
if str(_root) not in sys.path:
    sys.path.insert(0, str(_root))

from agents.guidance import load_guidance_files


def estimate_tokens(text: str) -> int:
    """Estimate token count using the ~4 chars per token heuristic."""
    if not text:
        return 0
    return len(text) // 4


def load_tony_prompt() -> str:
    """Load Tony's system prompt."""
    prompt_path = Path(__file__).parent.parent / "agents" / "tony" / "prompts.md"
    if prompt_path.exists():
        return prompt_path.read_text()
    return ""


def load_jarvis_prompt() -> str:
    """Load Jarvis's system prompt."""
    prompt_path = Path(__file__).parent.parent / "agents" / "jarvis" / "prompts.md"
    if prompt_path.exists():
        return prompt_path.read_text()
    return ""


def audit_tony_prompt_size(num_history_runs: int = 3, avg_run_tokens: int = 1500) -> dict:
    """Audit Tony's total prompt size configuration."""
    system_prompt = load_tony_prompt()
    core_context = load_guidance_files()

    system_tokens = estimate_tokens(system_prompt)
    core_tokens = estimate_tokens(core_context)
    history_tokens = num_history_runs * avg_run_tokens
    base_tokens = system_tokens + core_tokens
    total_with_history = base_tokens + history_tokens

    return {
        "agent": "tony",
        "system_prompt_tokens": system_tokens,
        "core_context_tokens": core_tokens,
        "base_prompt_tokens": base_tokens,
        "num_history_runs": num_history_runs,
        "avg_run_tokens": avg_run_tokens,
        "history_tokens": history_tokens,
        "total_estimated_tokens": total_with_history,
        "matches_7277": abs(total_with_history - 7277) < 500,
    }


def audit_jarvis_prompt_size(num_history_runs: int = 5, avg_run_tokens: int = 800) -> dict:
    """Audit Jarvis's total prompt size configuration."""
    system_prompt = load_jarvis_prompt()
    core_context = load_guidance_files()

    system_tokens = estimate_tokens(system_prompt)
    core_tokens = estimate_tokens(core_context)
    history_tokens = num_history_runs * avg_run_tokens
    base_tokens = system_tokens + core_tokens
    total_with_history = base_tokens + history_tokens

    return {
        "agent": "jarvis",
        "system_prompt_tokens": system_tokens,
        "core_context_tokens": core_tokens,
        "base_prompt_tokens": base_tokens,
        "num_history_runs": num_history_runs,
        "avg_run_tokens": avg_run_tokens,
        "history_tokens": history_tokens,
        "total_estimated_tokens": total_with_history,
        "matches_7277": abs(total_with_history - 7277) < 500,
    }


def print_audit_report():
    """Print a formatted audit report comparing Tony and Jarvis prompt sizes."""
    print("=" * 70)
    print("AGNO TOKEN AUDIT REPORT")
    print("=" * 70)
    print()

    tony_audit = audit_tony_prompt_size()
    jarvis_audit = audit_jarvis_prompt_size()

    print("TONY (port 8081 - Qwopus 35B)")
    print("-" * 40)
    print(f"  System prompt:        {tony_audit['system_prompt_tokens']:>6} tokens")
    print(f"  Core context:         {tony_audit['core_context_tokens']:>6} tokens")
    print(f"  Base prompt subtotal: {tony_audit['base_prompt_tokens']:>6} tokens")
    print(f"  History runs:         {tony_audit['num_history_runs']:>6} runs")
    print(f"  History contribution: {tony_audit['history_tokens']:>6} tokens")
    print(f"  TOTAL ESTIMATED:      {tony_audit['total_estimated_tokens']:>6} tokens")
    print(f"  Matches 7277 tokens:  {tony_audit['matches_7277']}")
    print()

    print("JARVIS (port 8082 - Qwopus-GLM-18B)")
    print("-" * 40)
    print(f"  System prompt:        {jarvis_audit['system_prompt_tokens']:>6} tokens")
    print(f"  Core context:         {jarvis_audit['core_context_tokens']:>6} tokens")
    print(f"  Base prompt subtotal: {jarvis_audit['base_prompt_tokens']:>6} tokens")
    print(f"  History runs:         {jarvis_audit['num_history_runs']:>6} runs")
    print(f"  History contribution: {jarvis_audit['history_tokens']:>6} tokens")
    print(f"  TOTAL ESTIMATED:      {jarvis_audit['total_estimated_tokens']:>6} tokens")
    print(f"  Matches 7277 tokens:  {jarvis_audit['matches_7277']}")
    print()

    print("=" * 70)
    print("ANALYSIS:")
    print("=" * 70)

    if tony_audit["matches_7277"]:
        print("✓ Tony's configuration (system + 3 history runs) could produce ~7277 tokens")
        print("  This suggests Tony's requests hitting port 8082 would be a misrouting issue.")
    elif jarvis_audit["matches_7277"]:
        print("✓ Jarvis's configuration could produce ~7277 tokens")
        print("  The large request may be legitimately from Jarvis.")
    else:
        print("? Neither agent's standard configuration matches 7277 tokens exactly.")
        print("  The request may include a large workfile or extensive tool results.")

    print()
    print("To confirm the origin, check the logs for:")
    print("  - [llm_call] entries with tokens=~7277")
    print("  - [factory:tony] entries with model_url containing :8082 (wrong port)")
    print("  - [factory:jarvis] entries with model_url containing :8081 (wrong port)")
    print("=" * 70)


if __name__ == "__main__":
    print_audit_report()
