import os

from dotenv import load_dotenv

load_dotenv()


# =========================================================
# PLAN CONFIGURATION
# =========================================================
# Every limit and price below is CONFIGURATION, not a
# final business decision. Change them here or override
# them with environment variables (see _env_int).
#
# message_limit   AI questions per period (one per
#                 answered user question)
# document_limit  documents uploaded per period
# period          "month" (calendar month, UTC) or
#                 "session" (one guest browser session)
# price           display text such as "₹499 / month";
#                 None means pricing isn't set yet


def _env_int(name: str, default: int) -> int:

    value = os.getenv(name)

    return int(value) if value and value.isdigit() else default


PLANS = {

    "guest": {
        "name": "Guest",
        "message_limit": _env_int("GUEST_MESSAGE_LIMIT", 5),
        "document_limit": _env_int("GUEST_DOCUMENT_LIMIT", 5),
        "period": "session",
        "price": None,
        "features": [
            "Travel knowledge and places search",
            "Ask questions about uploaded documents",
            "Chats are not saved",
        ],
        "purchasable": False,
    },

    "free": {
        "name": "Free",
        "message_limit": _env_int("FREE_MESSAGE_LIMIT", 50),
        "document_limit": _env_int("FREE_DOCUMENT_LIMIT", 10),
        "period": "month",
        "price": os.getenv("FREE_PLAN_PRICE", "Free"),
        "features": [
            "Travel knowledge (RAG) and live places search",
            "Basic document question answering",
            "Saved chat history",
        ],
        "purchasable": False,
    },

    "pro": {
        "name": "Pro",
        "message_limit": _env_int("PRO_MESSAGE_LIMIT", 1000),
        "document_limit": _env_int("PRO_DOCUMENT_LIMIT", 200),
        "period": "month",
        # Display text. Derived from "billing" below when
        # not set explicitly.
        "price": os.getenv("PRO_PLAN_PRICE") or None,
        # What the payment provider charges. All unset
        # until you decide on pricing; checkout refuses
        # to start while any required value is missing.
        "billing": {
            # Smallest currency unit, e.g. paise for INR.
            "amount": _env_int("PRO_PRICE_AMOUNT", 0) or None,
            "currency": os.getenv("PRO_PRICE_CURRENCY") or None,
            # daily | weekly | monthly | quarterly | yearly
            "period": os.getenv("PRO_BILLING_PERIOD") or None,
            "interval": _env_int("PRO_BILLING_INTERVAL", 1),
            # Number of billing cycles (required by
            # Razorpay subscriptions).
            "cycles": _env_int("PRO_BILLING_CYCLES", 0) or None,
        },
        "features": [
            "Higher AI usage allowance",
            "More document uploads for document RAG",
            "Saved chat history",
            "Travel knowledge and live places search",
        ],
        "purchasable": True,
    },
}

DEFAULT_USER_PLAN = "free"

GUEST_PLAN = "guest"


REQUIRED_BILLING_FIELDS = ["amount", "currency", "period", "cycles"]

BILLING_ENV_NAMES = {
    "amount": "PRO_PRICE_AMOUNT",
    "currency": "PRO_PRICE_CURRENCY",
    "period": "PRO_BILLING_PERIOD",
    "cycles": "PRO_BILLING_CYCLES",
}


def missing_billing_config(plan_name: str) -> list[str]:
    """
    Environment variables still needed before this plan
    can be sold.
    """

    billing = PLANS.get(plan_name, {}).get("billing") or {}

    return [
        BILLING_ENV_NAMES[field]
        for field in REQUIRED_BILLING_FIELDS
        if not billing.get(field)
    ]


def _price_text(plan: dict) -> str | None:

    if plan.get("price"):
        return plan["price"]

    if not plan.get("billing") or missing_billing_config(plan["id"]):
        return None

    billing = plan["billing"]

    every = (
        billing["period"]
        if billing["interval"] == 1
        else f"{billing['interval']} × {billing['period']}"
    )

    return (
        f"{billing['currency']} {billing['amount'] / 100:,.2f}"
        f" / {every}"
    )


def get_plan(plan_name: str) -> dict:

    plan = {
        "id": plan_name,
        **PLANS.get(plan_name, PLANS[DEFAULT_USER_PLAN])
    }

    plan["price"] = _price_text(plan)

    return plan


def public_plans() -> list[dict]:
    """
    Plans shown on the pricing page (not the guest plan).
    """

    return [
        get_plan(plan_name)
        for plan_name in PLANS
        if plan_name != GUEST_PLAN
    ]
