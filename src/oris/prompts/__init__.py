"""Version-controlled system prompts used by ORIS model tasks."""

from importlib.resources import files


def load_system_prompt(filename: str) -> str:
    """Load one packaged system prompt and reject an empty file."""
    prompt = files(__package__).joinpath(filename).read_text(encoding="utf-8").strip()
    if not prompt:
        raise ValueError(f"System prompt is empty: {filename}")
    return prompt
