"""Shared company guidance files loaded into agent context."""

from pathlib import Path

GUIDANCE_PATHS = (
    "knowledge/core/goals.md",
    "knowledge/core/compliance.md",
    "knowledge/core/voice.md",
)


def load_guidance_files() -> str:
    """Load global guidance markdown files as additional context."""
    sections = []
    for path in GUIDANCE_PATHS:
        if Path(path).exists():
            with open(path) as f:
                sections.append(f"## {path}\n{f.read()}")
    return "\n\n".join(sections)
