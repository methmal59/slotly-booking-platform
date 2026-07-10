"""Excerpt: per-salon tenant routing in the AI service's internal API client.

Every inbound WhatsApp webhook carries Meta's stable numeric ``phone_number_id``
for the receiving business number. That id — never the display phone number,
which can change — is the multi-tenant routing key: it resolves to the salon
(tenant) whose data, language, and WhatsApp credentials the turn runs under.

Design notes visible in this small piece:
- The AI service holds no DB credentials; resolution is an HTTP call to the
  core API, authenticated with a shared service key.
- A short in-process TTL cache keeps webhook turns fast while letting salon
  config changes (language, handoff window, bot on/off) propagate within
  minutes with no restart.
- Every config field defaults FAIL-OPEN: an API omission or hiccup must never
  silence a live salon's bot mid-day.
"""

from __future__ import annotations

import time
from dataclasses import dataclass


@dataclass
class TenantInfo:
    id: int
    name: str
    language: str = "si"  # business default reply language
    # Coexistence handoff: when staff reply from their own WhatsApp Business
    # app, the bot goes silent for that customer for a configurable window.
    handoff_enabled: bool = True
    handoff_window_minutes: int = 120
    # The salon's WhatsApp-AI on/off switch. Default ON (fail-open) so an
    # omitted flag or API hiccup never silences a live salon; an explicitly
    # off salon gets total silence, gated before any LLM call or send.
    whatsapp_ai_enabled: bool = True


# routing key -> (expiry_monotonic, TenantInfo). A short TTL so a salon changing
# its config is picked up within minutes WITHOUT a process restart, while still
# avoiding a round-trip on every webhook turn.
_TENANT_CACHE_TTL_SECONDS = 300  # 5 minutes
_tenant_cache: dict[str, tuple[float, TenantInfo]] = {}


def resolve_tenant(phone_number_id: str) -> TenantInfo | None:
    """Resolve a salon by its stable Meta numeric phone_number_id.

    Returns None for a confirmed-unknown number (no business owns it — the
    turn is dropped silently rather than replied to from an unrelated number).
    A resolve *failure* is raised and handled fail-open by the caller.
    """
    normalised = phone_number_id.lstrip("+")
    now = time.monotonic()
    cached = _tenant_cache.get(normalised)
    if cached and cached[0] > now:
        return cached[1]

    r = _client().get("/tenant/resolve", params={"whatsapp_number": normalised})
    if r.status_code == 404:
        logger.warning("No active tenant for phone_number_id: %s", phone_number_id)
        return None
    r.raise_for_status()
    data = r.json()
    tenant = TenantInfo(
        id=data["id"],
        name=data["name"],
        language=data.get("language") or "si",
        handoff_enabled=bool(data.get("handoff_enabled", True)),
        handoff_window_minutes=int(data.get("handoff_window_minutes") or 120),
        whatsapp_ai_enabled=bool(data.get("whatsapp_ai_enabled", True)),
    )
    _tenant_cache[normalised] = (now + _TENANT_CACHE_TTL_SECONDS, tenant)
    return tenant
