"""Excerpt from the production AI service (unmodified).

Booking-level idempotency guard — Tier-1 #2 (double-tap double-booking).

A *genuine* double-tap (two real WhatsApp messages the customer sent, each with
its own `wa_id`) passes the per-`wa_id` webhook dedup in `main.webhook_receive`
and is processed twice. Without a guard, both executions write — two identical
appointments, two cancels, or two new reschedule bookings.

Option A (chosen 2026-06-03): claim a short-lived Redis `SET NX` lock keyed on the
*booking identity* immediately before each write. The first caller claims it and
proceeds; a duplicate sees the key already set and is told it's already handled
(no second API call). The lock is **released on write failure** so a genuine retry
after a real error still works; on success it lingers for the TTL, long enough to
absorb a near-simultaneous second tap.

Reuses the exact `SET NX` idiom already used for `wa_dedup`. Shared Redis, so the
two background tasks of a real double-tap contend on the same key.
"""

from __future__ import annotations

from db.redis_client import get_redis

# Must comfortably exceed the worst-case end-to-end processing time of a single
# turn (mark-read + 2 Gemini calls + several API round-trips, each with a 10s httpx
# timeout) so the lock can't expire mid-write and let a near-duplicate second tap
# through. 5 minutes covers p99 latency and aligns with the other 5-min windows
# (creds + bookings caches). A booking for the *same* customer/branch/slot/services
# *and staff* within this window is a duplicate, not a new visit, so no legit
# booking is blocked. The employee is part of the identity: the same slot with a
# different staff member is a distinct booking and must not be treated as a dup.
_LOCK_TTL = 300  # seconds (5 minutes)


def _claim(key: str, ttl: int = _LOCK_TTL) -> bool:
    """True if this caller claimed the lock (proceed); False if it's a duplicate."""
    return get_redis().set(key, "1", ex=ttl, nx=True) is not None


def _release(key: str) -> None:
    get_redis().delete(key)


# ── key builders ─────────────────────────────────────────────────────────────

def _book_key(tenant_id, customer_id, branch_id, date, start_time, service_ids,
              employee_id=None) -> str:
    sids = "-".join(str(s) for s in sorted(service_ids or []))
    emp = employee_id if employee_id is not None else ""
    return f"book_lock:{tenant_id}:{customer_id}:{branch_id}:{date}:{start_time}:{sids}:{emp}"


def _cancel_key(tenant_id, appointment_id) -> str:
    return f"cancel_lock:{tenant_id}:{appointment_id}"


def _reschedule_key(tenant_id, appointment_id) -> str:
    return f"reschedule_lock:{tenant_id}:{appointment_id}"


# ── booking ──────────────────────────────────────────────────────────────────

def claim_booking(tenant_id, customer_id, branch_id, date, start_time, service_ids,
                  employee_id=None) -> bool:
    return _claim(_book_key(tenant_id, customer_id, branch_id, date, start_time,
                            service_ids, employee_id))


def release_booking(tenant_id, customer_id, branch_id, date, start_time, service_ids,
                    employee_id=None) -> None:
    _release(_book_key(tenant_id, customer_id, branch_id, date, start_time,
                       service_ids, employee_id))


# ── cancellation ─────────────────────────────────────────────────────────────

def claim_cancel(tenant_id, appointment_id) -> bool:
    return _claim(_cancel_key(tenant_id, appointment_id))


def release_cancel(tenant_id, appointment_id) -> None:
    _release(_cancel_key(tenant_id, appointment_id))


# ── reschedule (whole cancel-old → book-new operation) ───────────────────────

def claim_reschedule(tenant_id, appointment_id) -> bool:
    return _claim(_reschedule_key(tenant_id, appointment_id))


def release_reschedule(tenant_id, appointment_id) -> None:
    _release(_reschedule_key(tenant_id, appointment_id))
