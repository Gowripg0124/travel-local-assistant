import contextvars
import uuid
from datetime import datetime, timezone

from services import database
from services.plans import (
    DEFAULT_USER_PLAN,
    GUEST_PLAN,
    PLANS,
    get_plan
)


# =========================================================
# CENTRAL USAGE MANAGEMENT
# =========================================================
# The backend is the source of truth for plan, usage and
# limits. Nothing here trusts values sent by the frontend.
#
# Counted:  one "message" per answered user question, and
#           one "document_upload" per stored document.
# Not counted: assistant replies, retrieval, embeddings,
#           routing, internal calls, failed answers.

MESSAGE = "message"

DOCUMENT_UPLOAD = "document_upload"

SUBSCRIPTION_STATUSES = {
    "free", "active", "canceled", "expired", "past_due"
}


# =========================================================
# TOKEN CAPTURE
# =========================================================
# Gemini responses already include token counts in
# usage_metadata. extract_text() in rag_service reports
# them here, so no extra calls are made. A request that
# never calls Gemini records no tokens (None).

_request_tokens = contextvars.ContextVar(
    "request_tokens",
    default=None
)


def start_token_tracking():

    _request_tokens.set(
        {"input_tokens": 0, "output_tokens": 0,
         "total_tokens": 0, "calls": 0}
    )


def add_tokens(usage_metadata):
    """
    Called with a Gemini response's usage_metadata.
    """

    tokens = _request_tokens.get()

    if tokens is None or not usage_metadata:
        return

    for key in ("input_tokens", "output_tokens", "total_tokens"):
        tokens[key] += usage_metadata.get(key) or 0

    tokens["calls"] += 1


def collected_tokens() -> dict:

    tokens = _request_tokens.get()

    if not tokens or not tokens["calls"]:
        return {
            "input_tokens": None,
            "output_tokens": None,
            "total_tokens": None
        }

    return {
        key: tokens[key]
        for key in ("input_tokens", "output_tokens", "total_tokens")
    }


# =========================================================
# PLANS & SUBSCRIPTIONS
# =========================================================

def _now() -> datetime:

    return datetime.now(timezone.utc)


def _period_bounds(period: str) -> tuple[str | None, str | None]:
    """
    (start, resets_at) as ISO strings. Guest sessions
    have no fixed period.
    """

    if period != "month":
        return None, None

    now = _now()

    start = now.replace(
        day=1, hour=0, minute=0, second=0, microsecond=0
    )

    if start.month == 12:
        reset = start.replace(year=start.year + 1, month=1)
    else:
        reset = start.replace(month=start.month + 1)

    return start.isoformat(), reset.isoformat()


def get_user_plan(user_id: int) -> tuple[str, dict | None]:
    """
    (plan id, subscription row). A paid plan applies only
    while the subscription is active, or canceled with
    paid time remaining; past_due and expired fall back
    to Free.
    """

    subscription = database.get_subscription(user_id)

    if subscription and subscription["plan_name"] in PLANS:

        now = _now().isoformat()

        expires_at = subscription["expires_at"]

        still_paid = expires_at is None or expires_at > now

        if subscription["status"] == "active" and still_paid:
            return subscription["plan_name"], subscription

        if (
            subscription["status"] == "canceled"
            and expires_at
            and expires_at > now
        ):
            return subscription["plan_name"], subscription

    return DEFAULT_USER_PLAN, subscription


def apply_subscription_event(event: dict):
    """
    Update a user's subscription from a VERIFIED payment
    provider event (see payment_service). Never call this
    with data that came from the frontend.
    """

    status = event["status"]

    if status not in SUBSCRIPTION_STATUSES:
        raise ValueError(f"Unknown subscription status: {status}")

    if event["plan_name"] not in PLANS:
        raise ValueError(f"Unknown plan: {event['plan_name']}")

    database.upsert_subscription(
        user_id=event["user_id"],
        plan_name=event["plan_name"],
        status=status,
        provider=event.get("provider"),
        subscription_id=event.get("subscription_id"),
        started_at=event.get("started_at"),
        expires_at=event.get("expires_at")
    )


def process_subscription_event(event: dict) -> str:
    """
    Apply a VERIFIED provider event at most once.

    Returns "applied", "duplicate" (same event id seen
    before), "stale" (older than an event already applied
    to this subscription), or "unknown_user".
    """

    if database.get_user_by_id(event["user_id"]) is None:
        return "unknown_user"

    event_id = event.get("event_id")

    if event_id and database.payment_event_exists(event_id):
        return "duplicate"

    latest = (
        database.latest_payment_event_at(event["subscription_id"])
        if event.get("subscription_id")
        else None
    )

    stale = bool(
        latest
        and event.get("event_created_at")
        and event["event_created_at"] < latest
    )

    if not stale:
        apply_subscription_event(event)

    # Recorded after applying, so a failed update is
    # retried by the provider instead of being skipped.
    if event_id:
        database.add_payment_event(
            event_id,
            event.get("provider") or "unknown",
            event.get("subscription_id"),
            event.get("event_created_at")
        )

    return "stale" if stale else "applied"


# =========================================================
# USAGE STATUS
# =========================================================

def _owner(user: dict | None, guest_key: str | None) -> dict:

    if user is not None:
        return {"user_id": user["id"]}

    return {"guest_key": guest_key}


def get_usage_status(
    user: dict | None = None,
    guest_key: str | None = None
) -> dict:
    """
    Current plan, usage and limits for a user or guest.
    """

    if user is not None:
        plan_id, subscription = get_user_plan(user["id"])
    else:
        plan_id, subscription = GUEST_PLAN, None

    plan = get_plan(plan_id)

    since, resets_at = _period_bounds(plan["period"])

    owner = _owner(user, guest_key)

    messages = database.summarize_usage(MESSAGE, since, **owner)

    documents = database.summarize_usage(
        DOCUMENT_UPLOAD, since, **owner
    )

    used = messages["count"]

    return {
        "plan": plan_id,
        "plan_name": plan["name"],
        "period": plan["period"],
        "resets_at": resets_at,
        "messages": {
            "used": used,
            "limit": plan["message_limit"],
            "remaining": max(plan["message_limit"] - used, 0)
        },
        "documents": {
            "used": documents["count"],
            "limit": plan["document_limit"],
            "remaining": max(
                plan["document_limit"] - documents["count"], 0
            )
        },
        "tokens": {
            "input_tokens": messages["input_tokens"],
            "output_tokens": messages["output_tokens"],
            "total_tokens": messages["total_tokens"]
        },
        "limit_reached": used >= plan["message_limit"],
        "subscription": (
            {
                "plan_name": subscription["plan_name"],
                "status": subscription["status"],
                "started_at": subscription["started_at"],
                "expires_at": subscription["expires_at"]
            }
            if subscription
            else None
        )
    }


def limit_reached_response(status: dict) -> dict:
    """
    Returned by /ask instead of an answer; Gemini is not
    called. Keeps the normal answer fields so existing
    clients still render it.
    """

    if status["plan"] == GUEST_PLAN:
        message = (
            "You've reached the guest message limit. Create an "
            "account or log in to continue chatting."
        )
    else:
        message = (
            f"You've reached your {status['plan_name']} plan "
            "limit. Upgrade your plan to continue using the "
            "assistant."
        )

    return {
        "limit_reached": True,
        "plan": status["plan"],
        "usage": {
            "used": status["messages"]["used"],
            "limit": status["messages"]["limit"]
        },
        "message": message,
        "route": "limit",
        "answer": message,
        "sources": [],
        "places": []
    }


# =========================================================
# RECORDING
# =========================================================

def new_request_id() -> str:

    return uuid.uuid4().hex


def record_message(
    request_id: str,
    user: dict | None = None,
    guest_key: str | None = None,
    conversation_id: int | None = None
):
    """
    One usage unit for an answered question, with the
    Gemini tokens collected during the request.
    """

    database.add_usage_record(
        request_id=request_id,
        usage_type=MESSAGE,
        conversation_id=conversation_id,
        **_owner(user, guest_key),
        **collected_tokens()
    )


def record_document_uploads(
    count: int,
    user: dict | None = None,
    guest_key: str | None = None,
    conversation_id: int | None = None
):

    request_id = new_request_id()

    for _ in range(count):

        database.add_usage_record(
            request_id=request_id,
            usage_type=DOCUMENT_UPLOAD,
            conversation_id=conversation_id,
            **_owner(user, guest_key)
        )
