import os
from typing import Protocol

from dotenv import load_dotenv

load_dotenv()


# =========================================================
# PAYMENT PROVIDER ABSTRACTION
# =========================================================
# No payment provider is integrated yet. To add one
# (e.g. a provider you choose later):
#
#   1. Implement PaymentProvider below in its own class,
#      reading secret keys from environment variables on
#      the BACKEND only.
#   2. Register it in get_payment_provider() and set
#      PAYMENT_PROVIDER in the server environment.
#   3. Point the provider's webhook at POST /billing/webhook.
#
# Subscriptions change ONLY through verify_webhook(),
# i.e. after the provider confirms a payment. Clicking
# "Upgrade" never grants a plan by itself.


class PaymentNotConfigured(Exception):
    """
    Raised when billing is used before a provider is set up.
    The message is safe to show to the user.
    """


class PaymentProviderError(Exception):
    """
    A provider call failed (API error, timeout, invalid
    state). The message is safe to show to the user;
    details are never included.
    """


class InvalidWebhook(Exception):
    """
    The webhook signature is missing or invalid.
    """


class PaymentProvider(Protocol):

    name: str

    def create_checkout_session(
        self,
        user: dict,
        plan_id: str
    ) -> str:
        """
        Start checkout; return the provider's checkout URL.
        """

    def create_portal_session(self, user: dict) -> str:
        """
        Return a URL where the user manages or cancels.
        """

    def verify_webhook(
        self,
        payload: bytes,
        headers: dict
    ) -> dict | None:
        """
        Verify the webhook signature with the provider's
        secret and translate it into a subscription event:

        {"user_id", "plan_name", "status", "provider",
         "subscription_id", "started_at", "expires_at",
         "event_id", "event_created_at"}

        event_id / event_created_at (optional) let the
        webhook endpoint skip duplicate and out-of-order
        deliveries.

        Return None for events that don't change a
        subscription. Raise InvalidWebhook on an invalid
        signature.
        """


def _razorpay_provider():

    # Imported lazily so the SDK is only needed when
    # Razorpay is the configured provider.
    from services.razorpay_payment_service import (
        RazorpayPaymentProvider
    )

    return RazorpayPaymentProvider()


# Registered providers: {"name": factory}
PROVIDERS: dict[str, callable] = {
    "razorpay": _razorpay_provider,
}


def get_payment_provider() -> PaymentProvider:

    name = (os.getenv("PAYMENT_PROVIDER") or "").strip().lower()

    if not name or name not in PROVIDERS:
        raise PaymentNotConfigured(
            "Online payments aren't set up yet, so upgrades "
            "aren't available right now. Your current plan "
            "is unchanged."
        )

    return PROVIDERS[name]()
