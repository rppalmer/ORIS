"""Version-controlled system prompts used by ORIS model tasks."""

from datetime import date
from importlib.resources import files


def load_system_prompt(filename: str) -> str:
    """Load one packaged system prompt and reject an empty file."""
    prompt = files(__package__).joinpath(filename).read_text(encoding="utf-8").strip()
    if not prompt:
        raise ValueError(f"System prompt is empty: {filename}")
    return prompt


def current_date_line(today: date | None = None) -> str:
    """State the calendar date in the wording the prompts refer back to.

    Separate from `with_current_date` because the search planner supplies the
    date in its human turn and its prompt is written against that placement.
    Both callers get the date from here so the wording has one definition.
    """
    resolved = today if today is not None else date.today()
    return f"Current date: {resolved.isoformat()}"


def with_current_date(system_prompt: str, today: date | None = None) -> str:
    """Append today's date to a system prompt, resolved at the moment of use.

    A model has no clock. Unsaid, its most recent idea of "now" is its training
    cutoff, so a specialist asked to weigh whether evidence is current has
    nothing to weigh it against, and direct chat answers as of a date months in
    the past. Observed: a Web Research answer placed a page of unknown vintage
    beside genuine current reporting with no basis to rank them.

    Resolved per call rather than folded into the packaged prompt at import,
    because the scheduler and a terminal session are long-running processes
    that outlive the day they started on.
    """
    return f"{system_prompt}\n{current_date_line(today)}"
