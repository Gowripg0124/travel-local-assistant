import json
import os
import sys
from datetime import datetime, timezone

import razorpay
import requests
from dotenv import load_dotenv

from services.payment_service import (
    InvalidWebhook,
    PaymentNotConfigured,
    PaymentProviderError
)
from services.plans import PLANS, get_plan, missing_billing_config

load_dotenv()


# =========================================================
# RAZORPAY — TEST MODE ONLY
# =========================================================
# Implements the PaymentProvider interface from
# payment_service. Only Razorpay TEST keys (rzp_test_...)
# are accepted, so no real money can be charged.
#
# Environment (backend only, never in Streamlit):
#   PAYMENT_PROVIDER=razorpay
#   RAZORPAY_KEY_ID=rzp_test_...        test key id
#   RAZORPAY_KEY_SECRET=...             test key secret
#   RAZORPAY_WEBHOOK_SECRET=...         webhook secret
#   RAZORPAY_PRO_PLAN_ID=plan_...       test-mode plan for Pro
#
# Pro pricing comes from services/plans.py
# (PRO_PRICE_AMOUNT, PRO_PRICE_CURRENCY, PRO_BILLING_PERIOD,
#  PRO_BILLING_INTERVAL, PRO_BILLING_CYCLES).

TEST_KEY_PREFIX = "rzp_test_"

# Seconds per Razorpay API call.
API_TIMEOUT = 15

# Razorpay subscription status -> application status.
# "created" and "authenticated" are not mapped: no paid
# period has started yet, so nothing changes (stays Free).
STATUS_MAP = {
    "active": "active",
    # Charge failed and Razorpay is retrying.
    "pending": "past_due",
    # All retries exhausted.
    "halted": "past_due",
    # Access continues until the paid period ends
    # (expires_at), per the existing policy.
    "cancelled": "canceled",
    "paused": "canceled",
    # All billing cycles finished.
    "completed": "expired",
    "expired": "expired",
}

GENERIC_ERROR = (
    "Couldn't reach the payment provider right now. "
    "Please try again in a moment."
)


def _log(message: str):

    # Never includes keys, secrets or payloads.
    print(f"[razorpay] {message}", file=sys.stderr)


def _iso(timestamp) -> str | None:

    if not timestamp:
        return None

    return datetime.fromtimestamp(
        int(timestamp),
        tz=timezone.utc
    ).isoformat()


def _plan_env_name(plan_id: str) -> str:

    return f"RAZORPAY_{plan_id.upper()}_PLAN_ID"


class RazorpayPaymentProvider:

    name = "razorpay"

    # Only test keys are accepted (see _client).
    test_mode = True

    # -----------------------------------------------------
    # CONFIGURATION
    # -----------------------------------------------------

    def _client(self) -> razorpay.Client:

        key_id = os.getenv("RAZORPAY_KEY_ID")
        key_secret = os.getenv("RAZORPAY_KEY_SECRET")

        if not key_id or not key_secret:
            raise PaymentNotConfigured(
                "Online payments aren't set up yet, so upgrades "
                "aren't available right now."
            )

        if not key_id.startswith(TEST_KEY_PREFIX):
            _log("refusing non-test key id (test mode only)")
            raise PaymentNotConfigured(
                "Payments are limited to Razorpay test mode, and "
                "the configured key isn't a test key."
            )

        return razorpay.Client(auth=(key_id, key_secret))

    def _razorpay_plan_id(self, plan_id: str) -> str:

        missing = missing_billing_config(plan_id)

        if missing:
            _log(f"pricing not configured: {', '.join(missing)}")
            raise PaymentNotConfigured(
                f"{PLANS[plan_id]['name']} pricing isn't "
                "configured yet, so upgrades aren't available."
            )

        razorpay_plan_id = os.getenv(_plan_env_name(plan_id))

        if not razorpay_plan_id:
            _log(f"{_plan_env_name(plan_id)} not set")
            raise PaymentNotConfigured(
                "The subscription plan isn't set up with the "
                "payment provider yet."
            )

        return razorpay_plan_id

    def _check_plan_matches(self, client, razorpay_plan_id, plan_id):
        """
        Refuse checkout if the Razorpay plan charges
        something other than the configured price.
        """

        billing = PLANS[plan_id]["billing"]

        remote = client.plan.fetch(
            razorpay_plan_id,
            timeout=API_TIMEOUT
        )

        item = remote.get("item") or {}

        if (
            item.get("amount") != billing["amount"]
            or item.get("currency") != billing["currency"]
            or remote.get("period") != billing["period"]
            or remote.get("interval") != billing["interval"]
        ):
            _log("Razorpay plan does not match configured pricing")
            raise PaymentNotConfigured(
                "The payment plan doesn't match the configured "
                "pricing, so upgrades are paused until it's fixed."
            )

    # -----------------------------------------------------
    # CHECKOUT
    # -----------------------------------------------------

    def create_checkout_session(self, user: dict, plan_id: str) -> str:
        """
        Create a Razorpay TEST subscription and return its
        hosted payment link. This grants nothing: the plan
        changes only when the verified webhook arrives.
        """

        razorpay_plan_id = self._razorpay_plan_id(plan_id)

        client = self._client()

        try:

            self._check_plan_matches(client, razorpay_plan_id, plan_id)

            subscription = client.subscription.create(
                {
                    "plan_id": razorpay_plan_id,
                    "total_count": PLANS[plan_id]["billing"]["cycles"],
                    "customer_notify": True,
                    # Read back from the verified webhook to
                    # find the user. Set only by the backend.
                    "notes": {
                        "app_user_id": str(user["id"]),
                        "app_plan": plan_id,
                    },
                },
                timeout=API_TIMEOUT
            )

        except PaymentNotConfigured:
            raise

        except (
            razorpay.errors.BadRequestError,
            razorpay.errors.GatewayError,
            razorpay.errors.ServerError,
            requests.exceptions.RequestException,
            ValueError,
        ) as e:
            _log(f"checkout failed: {type(e).__name__}")
            raise PaymentProviderError(GENERIC_ERROR)

        checkout_url = subscription.get("short_url")

        if not checkout_url:
            _log("subscription created without a payment link")
            raise PaymentProviderError(GENERIC_ERROR)

        _log(f"created subscription {subscription.get('id')}")

        return checkout_url

    # -----------------------------------------------------
    # PORTAL
    # -----------------------------------------------------

    def create_portal_session(self, user: dict) -> str:
        """
        Razorpay has no hosted customer portal for
        subscriptions, so none is faked here.
        """

        raise PaymentNotConfigured(
            "Managing or cancelling your subscription from the "
            "app isn't available yet."
        )

    # -----------------------------------------------------
    # WEBHOOK
    # -----------------------------------------------------

    def verify_webhook(self, payload: bytes, headers: dict) -> dict | None:

        secret = os.getenv("RAZORPAY_WEBHOOK_SECRET")

        if not secret:
            raise PaymentNotConfigured(
                "Payment webhooks aren't configured."
            )

        headers = {key.lower(): value for key, value in headers.items()}

        signature = headers.get("x-razorpay-signature")

        if not signature:
            raise InvalidWebhook("missing signature")

        body = payload.decode("utf-8")

        try:

            razorpay.Client().utility.verify_webhook_signature(
                body,
                signature,
                secret
            )

        except razorpay.errors.SignatureVerificationError:
            raise InvalidWebhook("bad signature")

        # ---- Verified from here on ----

        data = json.loads(body)

        event_name = data.get("event", "")

        # Payment failures also move the subscription to
        # "pending"/"halted", which arrive as subscription
        # events; other events don't change access.
        if not event_name.startswith("subscription."):
            return None

        entity = (
            data.get("payload", {})
            .get("subscription", {})
            .get("entity", {})
        )

        status = STATUS_MAP.get(entity.get("status"))

        notes = entity.get("notes") or {}

        plan_id = notes.get("app_plan")

        user_id = notes.get("app_user_id")

        if (
            not status
            or plan_id not in PLANS
            or not str(user_id or "").isdigit()
        ):
            _log(f"ignored {event_name} (status/notes not applicable)")
            return None

        # Only the configured Razorpay plan can grant a plan.
        if entity.get("plan_id") != os.getenv(_plan_env_name(plan_id)):
            _log(f"ignored {event_name} for an unknown plan")
            return None

        ended_at = _iso(entity.get("ended_at"))

        current_end = _iso(entity.get("current_end"))

        if status == "expired":
            expires_at = ended_at or current_end or datetime.now(
                timezone.utc
            ).isoformat()
        elif status == "canceled":
            # Immediate cancellation ends access now;
            # cancel-at-cycle-end keeps it until period end.
            expires_at = ended_at or current_end
        else:
            expires_at = current_end

        return {
            "user_id": int(user_id),
            "plan_name": plan_id,
            "status": status,
            "provider": self.name,
            "subscription_id": entity.get("id"),
            "started_at": _iso(
                entity.get("start_at") or entity.get("current_start")
            ),
            "expires_at": expires_at,
            "event_id": headers.get("x-razorpay-event-id"),
            "event_created_at": _iso(data.get("created_at")),
        }


# =========================================================
# ONE-TIME SETUP: create the TEST plan from plans.py
# =========================================================
#   python -m services.razorpay_payment_service create-plan pro
# Prints the plan id to put in RAZORPAY_PRO_PLAN_ID.

def create_test_plan(plan_id: str) -> str:

    missing = missing_billing_config(plan_id)

    if missing:
        raise SystemExit(
            "Set these first (in .env): " + ", ".join(missing)
        )

    billing = PLANS[plan_id]["billing"]

    plan = get_plan(plan_id)

    created = RazorpayPaymentProvider()._client().plan.create(
        {
            "period": billing["period"],
            "interval": billing["interval"],
            "item": {
                "name": f"{plan['name']} plan",
                "amount": billing["amount"],
                "currency": billing["currency"],
            },
            "notes": {"app_plan": plan_id},
        },
        timeout=API_TIMEOUT
    )

    return created["id"]


if __name__ == "__main__":

    if len(sys.argv) == 3 and sys.argv[1] == "create-plan":

        plan_name = sys.argv[2]

        print(
            f"Created Razorpay TEST plan: {create_test_plan(plan_name)}\n"
            f"Add it to .env as {_plan_env_name(plan_name)}=<that id>"
        )

    else:

        print(
            "Usage: python -m services.razorpay_payment_service "
            "create-plan pro"
        )
