"""
Razorpay TEST-mode billing tests.

All Razorpay API calls are mocked; no network calls are
made and no real payment happens. Webhook signatures are
computed with a dummy secret and checked by the real SDK.

Run:  python test/test_razorpay_billing.py
"""

import hashlib
import hmac
import json
import os
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

# ---------------------------------------------------------
# Test configuration (dummy values, set before imports)
# ---------------------------------------------------------

TMP = tempfile.mkdtemp()

DUMMY_KEY_ID = "rzp_test_dummykey"
DUMMY_KEY_SECRET = "dummy-key-secret-123"
DUMMY_WEBHOOK_SECRET = "dummy-webhook-secret-456"
DUMMY_PLAN_ID = "plan_testdummy"

os.environ.update({
    "DATABASE_PATH": os.path.join(TMP, "billing_test.db"),
    # Keep test documents away from real uploads.
    "UPLOADS_VECTORSTORE_DIR": os.path.join(TMP, "uploads"),
    "MOCK_LLM": "true",
    "PAYMENT_PROVIDER": "razorpay",
    "RAZORPAY_KEY_ID": DUMMY_KEY_ID,
    "RAZORPAY_KEY_SECRET": DUMMY_KEY_SECRET,
    "RAZORPAY_WEBHOOK_SECRET": DUMMY_WEBHOOK_SECRET,
    "RAZORPAY_PRO_PLAN_ID": DUMMY_PLAN_ID,
    "PRO_PRICE_AMOUNT": "10000",
    "PRO_PRICE_CURRENCY": "INR",
    "PRO_BILLING_PERIOD": "monthly",
    "PRO_BILLING_INTERVAL": "1",
    "PRO_BILLING_CYCLES": "12",
    "FREE_MESSAGE_LIMIT": "3",
    "PRO_MESSAGE_LIMIT": "7",
})

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import razorpay  # noqa: E402
import requests  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from api.main import app  # noqa: E402
from services import database, usage_service  # noqa: E402
from services import razorpay_payment_service as rzp  # noqa: E402


SECRETS = [DUMMY_KEY_SECRET, DUMMY_WEBHOOK_SECRET]

# Real client class, kept before patching.
REAL_CLIENT = razorpay.Client


# ---------------------------------------------------------
# Mocked Razorpay client
# ---------------------------------------------------------

class FakeRazorpay:
    """
    Stands in for razorpay.Client's plan / subscription
    resources. Records calls; never touches the network.
    """

    created = []

    plan_amount = 10000

    fail_with = None

    def __init__(self, *args, **kwargs):

        self.plan = mock.Mock()
        self.plan.fetch.side_effect = self._fetch_plan

        self.subscription = mock.Mock()
        self.subscription.create.side_effect = self._create_subscription

        # Real signature verification (pure HMAC, offline).
        self.utility = REAL_CLIENT().utility

    def _fetch_plan(self, plan_id, **kwargs):

        if FakeRazorpay.fail_with:
            raise FakeRazorpay.fail_with

        return {
            "id": plan_id,
            "period": "monthly",
            "interval": 1,
            "item": {
                "amount": FakeRazorpay.plan_amount,
                "currency": "INR",
            },
        }

    def _create_subscription(self, data, **kwargs):

        FakeRazorpay.created.append({"data": data, "kwargs": kwargs})

        return {
            "id": f"sub_test{len(FakeRazorpay.created)}",
            "status": "created",
            "short_url": "https://rzp.io/i/testcheckout",
        }


def sign(body: bytes) -> str:

    return hmac.new(
        DUMMY_WEBHOOK_SECRET.encode(),
        body,
        hashlib.sha256
    ).hexdigest()


def webhook_body(
    event, user_id, status, sub_id="sub_test1",
    plan_id=DUMMY_PLAN_ID, created_at=None,
    current_end=None, ended_at=None
) -> bytes:

    now = int(time.time())

    return json.dumps({
        "entity": "event",
        "event": event,
        "created_at": created_at or now,
        "payload": {
            "subscription": {
                "entity": {
                    "id": sub_id,
                    "plan_id": plan_id,
                    "status": status,
                    "start_at": now,
                    "current_start": now,
                    "current_end": current_end or now + 30 * 86400,
                    "ended_at": ended_at,
                    "notes": {
                        "app_user_id": str(user_id),
                        "app_plan": "pro",
                    },
                }
            }
        },
    }).encode()


class RazorpayBillingTests(unittest.TestCase):

    @classmethod
    def setUpClass(cls):

        cls.patcher = mock.patch.object(rzp.razorpay, "Client", FakeRazorpay)
        cls.patcher.start()

        cls.client = TestClient(app)

        def signup(email):
            token = cls.client.post(
                "/auth/signup",
                json={"email": email, "password": "password-123"}
            ).json()["token"]
            headers = {"Authorization": f"Bearer {token}"}
            user_id = cls.client.get("/auth/me", headers=headers).json()["id"]
            return headers, user_id

        cls.A, cls.a_id = signup("user.a@example.com")
        cls.B, cls.b_id = signup("user.b@example.com")

    @classmethod
    def tearDownClass(cls):

        cls.patcher.stop()

    def setUp(self):

        FakeRazorpay.created = []
        FakeRazorpay.plan_amount = 10000
        FakeRazorpay.fail_with = None

    # helpers

    def post_webhook(self, body, event_id=None, signature=None):

        headers = {
            "X-Razorpay-Signature": signature or sign(body),
            "Content-Type": "application/json",
        }

        if event_id:
            headers["x-razorpay-event-id"] = event_id

        return self.client.post(
            "/billing/webhook",
            content=body,
            headers=headers
        )

    def plan_of(self, headers):

        return self.client.get("/account", headers=headers).json()["usage"]

    def reset_subscription(self, user_id):

        with database.get_connection() as connection:
            connection.execute(
                "DELETE FROM subscriptions WHERE user_id = ?",
                (user_id,)
            )

    def assert_no_secrets(self, response):

        for secret in SECRETS:
            self.assertNotIn(secret, response.text)

    # 1
    def test_01_free_user_can_view_plans(self):

        response = self.client.get("/plans")

        pro = [plan for plan in response.json() if plan["id"] == "pro"][0]

        self.assertEqual(response.status_code, 200)
        self.assertEqual(pro["price"], "INR 100.00 / monthly")
        self.assertEqual(pro["message_limit"], 7)
        self.assert_no_secrets(response)

    # 2, 3
    def test_02_checkout_uses_razorpay_test_provider(self):

        self.reset_subscription(self.a_id)

        response = self.client.post(
            "/billing/checkout", json={"plan": "pro"}, headers=self.A
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response.json(),
            {"checkout_url": "https://rzp.io/i/testcheckout", "test_mode": True}
        )

        call = FakeRazorpay.created[0]

        self.assertEqual(call["data"]["plan_id"], DUMMY_PLAN_ID)
        self.assertEqual(call["data"]["total_count"], 12)
        self.assertEqual(
            call["data"]["notes"],
            {"app_user_id": str(self.a_id), "app_plan": "pro"}
        )
        self.assertEqual(call["kwargs"]["timeout"], rzp.API_TIMEOUT)
        self.assert_no_secrets(response)

    # 4
    def test_04_checkout_does_not_grant_pro(self):

        self.reset_subscription(self.a_id)

        self.client.post(
            "/billing/checkout",
            json={"plan": "pro", "status": "active"},
            headers=self.A
        )

        self.assertEqual(self.plan_of(self.A)["plan"], "free")

        self.assertEqual(
            self.client.post(
                "/billing/checkout", json={"plan": "free"}, headers=self.A
            ).status_code,
            400
        )

        self.assertEqual(
            self.client.post("/billing/checkout", json={"plan": "pro"}).status_code,
            401
        )

    # 5
    def test_05_invalid_webhook_signature_rejected(self):

        self.reset_subscription(self.a_id)

        body = webhook_body("subscription.activated", self.a_id, "active")

        bad = self.post_webhook(body, signature="0" * 64)
        missing = self.client.post("/billing/webhook", content=body)
        tampered = self.post_webhook(
            body.replace(b'"active"', b'"active" '), signature=sign(body)
        )

        for response in (bad, missing, tampered):
            self.assertEqual(response.status_code, 400)
            self.assert_no_secrets(response)

        self.assertEqual(self.plan_of(self.A)["plan"], "free")

    # 6, 10
    def test_06_valid_webhook_activates_pro_with_pro_limits(self):

        self.reset_subscription(self.a_id)

        response = self.post_webhook(
            webhook_body("subscription.activated", self.a_id, "active"),
            event_id="evt_activate_1"
        )

        self.assertEqual(response.json()["result"], "applied")

        usage = self.plan_of(self.A)

        self.assertEqual(usage["plan"], "pro")
        self.assertEqual(usage["messages"]["limit"], 7)
        self.assertEqual(usage["subscription"]["status"], "active")

        row = database.get_subscription(self.a_id)

        self.assertEqual(row["provider"], "razorpay")
        self.assertEqual(row["subscription_id"], "sub_test1")

    # 7
    def test_07_duplicate_and_stale_webhooks_are_safe(self):

        self.reset_subscription(self.a_id)

        now = int(time.time())

        activate = webhook_body(
            "subscription.activated", self.a_id, "active",
            sub_id="sub_dup", created_at=now
        )

        self.assertEqual(
            self.post_webhook(activate, "evt_dup_1").json()["result"],
            "applied"
        )
        self.assertEqual(
            self.post_webhook(activate, "evt_dup_1").json()["result"],
            "duplicate"
        )

        cancel = webhook_body(
            "subscription.cancelled", self.a_id, "cancelled",
            sub_id="sub_dup", created_at=now + 10, ended_at=now + 10
        )

        self.post_webhook(cancel, "evt_dup_2")

        # An older "charged" event arriving late must not
        # undo the cancellation.
        late_charge = webhook_body(
            "subscription.charged", self.a_id, "active",
            sub_id="sub_dup", created_at=now + 5
        )

        self.assertEqual(
            self.post_webhook(late_charge, "evt_dup_3").json()["result"],
            "stale"
        )
        self.assertEqual(database.get_subscription(self.a_id)["status"], "canceled")

    # 8
    def test_08_cancellation_keeps_access_until_period_end(self):

        self.reset_subscription(self.a_id)

        period_end = int(time.time()) + 10 * 86400

        self.post_webhook(
            webhook_body(
                "subscription.cancelled", self.a_id, "cancelled",
                sub_id="sub_cancel", current_end=period_end
            ),
            "evt_cancel_1"
        )

        usage = self.plan_of(self.A)

        self.assertEqual(usage["subscription"]["status"], "canceled")
        self.assertEqual(usage["plan"], "pro")

        # Immediate cancellation (ended_at now) ends access.
        self.post_webhook(
            webhook_body(
                "subscription.cancelled", self.a_id, "cancelled",
                sub_id="sub_cancel_now", ended_at=int(time.time()) - 1
            ),
            "evt_cancel_2"
        )

        self.assertEqual(self.plan_of(self.A)["plan"], "free")

    # 9
    def test_09_expired_completed_and_past_due_fall_back_to_free(self):

        for number, (event, status, expected) in enumerate([
            ("subscription.completed", "completed", "expired"),
            ("subscription.pending", "pending", "past_due"),
            ("subscription.halted", "halted", "past_due"),
        ]):

            self.reset_subscription(self.a_id)

            self.post_webhook(
                webhook_body(event, self.a_id, status, sub_id=f"sub_s{number}"),
                f"evt_state_{number}"
            )

            usage = self.plan_of(self.A)

            self.assertEqual(usage["subscription"]["status"], expected)
            self.assertEqual(usage["plan"], "free")
            self.assertEqual(usage["messages"]["limit"], 3)

    # 11
    def test_11_free_user_gets_free_limits_and_is_blocked(self):

        usage = self.plan_of(self.B)

        self.assertEqual((usage["plan"], usage["messages"]["limit"]), ("free", 3))

        for _ in range(3):
            self.client.post(
                "/ask",
                json={"question": "What is Coimbatore famous for?"},
                headers=self.B
            )

        blocked = self.client.post(
            "/ask", json={"question": "What is RAG?"}, headers=self.B
        ).json()

        self.assertTrue(blocked["limit_reached"])

    # 12
    def test_12_user_cannot_modify_another_users_subscription(self):

        self.reset_subscription(self.b_id)

        # A's checkout only ever tags A's own user id.
        self.reset_subscription(self.a_id)
        self.client.post(
            "/billing/checkout",
            json={"plan": "pro", "user_id": self.b_id},
            headers=self.A
        )

        self.assertEqual(
            FakeRazorpay.created[-1]["data"]["notes"]["app_user_id"],
            str(self.a_id)
        )

        # A forged webhook for B without the secret fails.
        forged = webhook_body("subscription.activated", self.b_id, "active")

        self.assertEqual(
            self.post_webhook(forged, signature=sign(b"other")).status_code,
            400
        )

        self.assertEqual(self.plan_of(self.B)["plan"], "free")

        # Webhooks for another Razorpay plan, or for a
        # user that doesn't exist, change nothing.
        other_plan = webhook_body(
            "subscription.activated", self.b_id, "active", plan_id="plan_other"
        )

        self.assertEqual(self.post_webhook(other_plan).json()["result"], "ignored")

        unknown = webhook_body("subscription.activated", 99999, "active")

        self.assertEqual(self.post_webhook(unknown).json()["result"], "unknown_user")

        self.assertEqual(self.plan_of(self.B)["plan"], "free")

    # 13
    def test_13_credentials_never_returned(self):

        responses = [
            self.client.get("/plans"),
            self.client.get("/account", headers=self.A),
            self.client.post("/billing/checkout", json={"plan": "pro"}, headers=self.A),
            self.client.post("/billing/portal", headers=self.A),
        ]

        for response in responses:
            self.assert_no_secrets(response)
            self.assertNotIn("RAZORPAY_", response.text)

    # 14
    def test_14_provider_errors_are_friendly(self):

        self.reset_subscription(self.a_id)

        for error in (
            razorpay.errors.BadRequestError("The id provided does not exist"),
            razorpay.errors.ServerError("upstream failure"),
            requests.exceptions.Timeout("read timed out"),
        ):

            FakeRazorpay.fail_with = error

            response = self.client.post(
                "/billing/checkout", json={"plan": "pro"}, headers=self.A
            )

            self.assertEqual(response.status_code, 502)
            self.assertIn("try again", response.json()["detail"])
            self.assertNotIn(str(error), response.text)

        self.assertEqual(self.plan_of(self.A)["plan"], "free")

    def test_15_plan_price_mismatch_blocks_checkout(self):

        self.reset_subscription(self.a_id)

        FakeRazorpay.plan_amount = 1

        response = self.client.post(
            "/billing/checkout", json={"plan": "pro"}, headers=self.A
        )

        self.assertEqual(response.status_code, 501)
        self.assertEqual(FakeRazorpay.created, [])

    def test_16_live_keys_refused(self):

        self.reset_subscription(self.a_id)

        with mock.patch.dict(os.environ, {"RAZORPAY_KEY_ID": "rzp_live_x"}):

            response = self.client.post(
                "/billing/checkout", json={"plan": "pro"}, headers=self.A
            )

        self.assertEqual(response.status_code, 501)
        self.assertIn("test mode", response.json()["detail"])
        self.assertEqual(FakeRazorpay.created, [])

    def test_17_missing_configuration(self):

        self.reset_subscription(self.a_id)

        with mock.patch.dict(os.environ, {"RAZORPAY_KEY_SECRET": ""}):
            self.assertEqual(
                self.client.post(
                    "/billing/checkout", json={"plan": "pro"}, headers=self.A
                ).status_code,
                501
            )

        with mock.patch.dict(os.environ, {"RAZORPAY_WEBHOOK_SECRET": ""}):
            body = webhook_body("subscription.activated", self.a_id, "active")
            self.assertEqual(self.post_webhook(body).status_code, 501)

        with mock.patch.dict(rzp.PLANS["pro"]["billing"], {"amount": None}):
            response = self.client.post(
                "/billing/checkout", json={"plan": "pro"}, headers=self.A
            )
            self.assertEqual(response.status_code, 501)
            self.assertIn("pricing isn't configured", response.json()["detail"])

    def test_18_pro_user_cannot_buy_pro_again_and_portal_is_honest(self):

        self.reset_subscription(self.a_id)

        self.post_webhook(
            webhook_body("subscription.activated", self.a_id, "active",
                         sub_id="sub_again"),
            "evt_again_1"
        )

        response = self.client.post(
            "/billing/checkout", json={"plan": "pro"}, headers=self.A
        )

        self.assertEqual(response.status_code, 409)

        portal = self.client.post("/billing/portal", headers=self.A)

        self.assertEqual(portal.status_code, 501)
        self.assertIn("isn't available yet", portal.json()["detail"])

    def test_19_irrelevant_verified_events_are_acknowledged(self):

        body = json.dumps({"event": "payment.captured", "payload": {}}).encode()

        response = self.post_webhook(body)

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["result"], "ignored")

        created = webhook_body(
            "subscription.authenticated", self.a_id, "authenticated"
        )

        self.assertEqual(self.post_webhook(created).json()["result"], "ignored")


if __name__ == "__main__":

    unittest.main(verbosity=2)
