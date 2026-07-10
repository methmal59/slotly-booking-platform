"""Excerpt from the production AI service (unmodified).

textutil.py — sanitisation for customer-typed free text that gets PERSISTED
and shown back (cancellation reasons, special requests / booking notes).

These strings travel far beyond the chat turn that produced them: stored in the
tenant DB, rendered in the web dashboard, echoed into WhatsApp confirmations,
folded into staff-notification template params, and fed back to the reply LLM
as context. SQL injection is already impossible (bound params API-side) and the
dashboard escapes on render — this layer is data hygiene plus prompt-injection
damage control: keep the note short, single-line, free of control/formatting
tricks, so a hostile or garbage message ("Ignore all previous instructions…",
"'; DROP TABLE bookings;--") persists as an inert, visibly-bounded snippet
instead of a wall of text that gets replayed everywhere. (The reply prompts in
tools/llm_tools.py additionally pin that data blocks are never instructions.)

Leaf module — no project imports — so any node/service can use it freely.
"""

from __future__ import annotations

import re

# C0/C1 control characters (except nothing — newlines/tabs collapse to spaces
# anyway) plus the invisible direction/zero-width marks that can reorder or
# hide text in the dashboard and WhatsApp.
_CONTROL_RE = re.compile(
    "[\\x00-\\x1f\\x7f-\\x9f"          # C0 + C1 control chars
    "\\u200b-\\u200f"                  # zero-width + LRM/RLM marks
    "\\u202a-\\u202e"                  # bidi embedding/override
    "\\u2066-\\u2069"                  # bidi isolates
    "\\ufeff]"                         # BOM / zero-width no-break space
)

# Default cap mirrors services/notifications._note_param's 200-char template cap
# so the stored value and the WhatsApp param stay the same text.
NOTE_MAX_CHARS = 200


def sanitize_customer_note(text: str | None, limit: int = NOTE_MAX_CHARS) -> str:
    """Collapse a customer-typed note/reason to one clean, bounded line.

    - strips control chars + zero-width/bidi marks,
    - collapses all whitespace runs (incl. newlines) to single spaces,
    - neutralises backticks (markdown/code-fence bait in dashboards + prompts),
    - caps the length (ellipsis marks the cut).

    Returns "" for empty/whitespace-only input — callers treat that as
    "no note given" exactly as before.
    """
    if not text:
        return ""
    t = _CONTROL_RE.sub(" ", str(text))
    t = t.replace("`", "'")
    t = " ".join(t.split())
    if limit and len(t) > limit:
        t = t[:limit].rstrip() + "…"
    return t
