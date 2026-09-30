from typing import Literal

from fastapi import Depends, FastAPI, Header, HTTPException, Request
from fastapi.responses import JSONResponse
from fastapi.security import (
    HTTPAuthorizationCredentials,
    HTTPBearer
)
from pydantic import BaseModel, Field
import httpx
from google.genai.errors import APIError as GoogleSDKError
from langchain_google_genai.chat_models import (
    ChatGoogleGenerativeAIError,
    GoogleRateLimitError
)

from rag.rag_service import ask_general, ask_rag
from services.router import route_question
from services.places_service import search_places
from services.query_analyzer import (
    analyze_query,
    extract_location_from_text,
    refers_to_travel_documents
)
from services.document_service import (
    add_documents,
    clear_documents,
    ask_documents,
    list_documents,
    move_documents
)
from services import auth_service, database, usage_service
from services.auth_service import AuthError
from services.payment_service import (
    InvalidWebhook,
    PaymentNotConfigured,
    PaymentProviderError,
    get_payment_provider
)
from services.plans import PLANS, public_plans
from services.conversation_service import (
    ConversationError,
    document_store_key,
    get_owned_conversation,
    guest_store_key,
    make_title
)

# Guest tokens are random strings generated per
# browser session; short ones are rejected.
MIN_GUEST_TOKEN_LENGTH = 32


# =========================================================
# FASTAPI APP
# =========================================================

app = FastAPI(
    title="Travel & Local Places Assistant",
    description="RAG and location-aware travel assistant",
    version="1.0.0"
)

# Create database tables if they don't exist yet.
database.init_db()


# =========================================================
# ERROR HANDLERS
# =========================================================

@app.exception_handler(AuthError)
def handle_auth_error(request, error: AuthError):

    return JSONResponse(
        status_code=error.status_code,
        content={"detail": str(error)}
    )


@app.exception_handler(ConversationError)
def handle_conversation_error(request, error: ConversationError):

    return JSONResponse(
        status_code=error.status_code,
        content={"detail": str(error)}
    )


# =========================================================
# AUTHENTICATION
# =========================================================

bearer_scheme = HTTPBearer(auto_error=False)


def get_current_user(
    credentials: HTTPAuthorizationCredentials | None = Depends(
        bearer_scheme
    )
) -> dict:
    """
    Resolve the user from the bearer token. The user id
    always comes from here, never from the request body.
    """

    user = auth_service.get_user_for_token(
        credentials.credentials if credentials else None
    )

    if user is None:
        raise HTTPException(
            status_code=401,
            detail="Please log in to continue."
        )

    return user


def get_optional_user(
    credentials: HTTPAuthorizationCredentials | None = Depends(
        bearer_scheme
    )
) -> dict | None:
    """
    None for guests (no token). A token that is sent
    but invalid or expired is still rejected.
    """

    if credentials is None:
        return None

    return get_current_user(credentials)


def get_guest_key(
    x_guest_token: str | None = Header(default=None)
) -> str:
    """
    Document store key for a guest session, from the
    random token the guest's browser session holds.
    """

    if not x_guest_token or len(x_guest_token) < MIN_GUEST_TOKEN_LENGTH:
        raise HTTPException(
            status_code=401,
            detail="Guest session missing. Please refresh the page."
        )

    return guest_store_key(x_guest_token)


# =========================================================
# REQUEST MODELS
# =========================================================

class ChatMessage(BaseModel):
    role: str
    content: str


class QuestionRequest(BaseModel):
    question: str
    history: list[ChatMessage] = Field(
        default_factory=list
    )
    # Set by the server from the conversation; any
    # value sent by the client is ignored.
    session_id: str | None = None
    conversation_id: int | None = None
    attachments: list[str] = Field(
        default_factory=list
    )


class UploadedDocument(BaseModel):
    name: str
    data_base64: str


class DocumentsRequest(BaseModel):
    documents: list[UploadedDocument]


class AuthRequest(BaseModel):
    email: str = ""
    password: str = ""


class ConversationRequest(BaseModel):
    # First question; used to build the title.
    first_question: str = ""


class ImportedMessage(BaseModel):
    role: Literal["user", "assistant"]
    content: str
    attachments: list[str] = Field(default_factory=list)
    route: str | None = None
    travel_sources: list = Field(default_factory=list)
    places: list = Field(default_factory=list)
    places_error: str | None = None


class ImportRequest(BaseModel):
    # The guest conversation to save on login.
    messages: list[ImportedMessage] = Field(max_length=100)


# =========================================================
# HOME
# =========================================================

@app.get("/")
def home():
    return {
        "message": (
            "Travel & Local Places Assistant "
            "API is running"
        )
    }


# =========================================================
# BUILD CONVERSATION HISTORY
# =========================================================

def build_history_context(
    history: list[ChatMessage]
) -> str:

    if not history:
        return ""

    history_text = []

    # Use only the last 6 messages
    for message in history[-6:]:
        role = message.role.upper()

        history_text.append(
            f"{role}: {message.content}"
        )

    return "\n".join(history_text)


# =========================================================
# AUTH ENDPOINTS
# =========================================================

@app.post("/auth/signup")
def signup(request: AuthRequest):

    return auth_service.signup(
        request.email,
        request.password
    )


@app.post("/auth/login")
def login(request: AuthRequest):

    return auth_service.login(
        request.email,
        request.password
    )


@app.post("/auth/logout")
def logout(
    credentials: HTTPAuthorizationCredentials | None = Depends(
        bearer_scheme
    )
):

    if credentials:
        auth_service.logout(credentials.credentials)

    return {"logged_out": True}


@app.get("/auth/me")
def me(user: dict = Depends(get_current_user)):

    return user


# =========================================================
# CONVERSATIONS
# =========================================================

def new_conversation(user_id: int, title: str) -> dict:
    """
    Create a conversation with an empty document store.
    Uploaded documents live in Chroma, separate from the
    database, so a store left over under the same ids
    (e.g. after the database was reset) is cleared.
    """

    conversation = database.create_conversation(user_id, title)

    clear_documents(
        document_store_key(user_id, conversation["id"])
    )

    return conversation

@app.get("/conversations")
def get_conversations(
    user: dict = Depends(get_current_user)
):

    return database.list_conversations(user["id"])


@app.post("/conversations")
def create_conversation(
    request: ConversationRequest,
    user: dict = Depends(get_current_user)
):

    return new_conversation(
        user["id"],
        make_title(request.first_question)
    )


@app.get("/conversations/{conversation_id}/messages")
def get_messages(
    conversation_id: int,
    user: dict = Depends(get_current_user)
):

    get_owned_conversation(conversation_id, user["id"])

    return database.list_messages(conversation_id)


@app.delete("/conversations/{conversation_id}")
def delete_conversation(
    conversation_id: int,
    user: dict = Depends(get_current_user)
):

    get_owned_conversation(conversation_id, user["id"])

    clear_documents(
        document_store_key(user["id"], conversation_id)
    )

    database.delete_conversation(conversation_id)

    return {"deleted": True}


# =========================================================
# UPLOADED DOCUMENTS (per conversation)
# =========================================================

def upload_within_limit(
    store_key: str,
    request: DocumentsRequest,
    user: dict | None = None,
    guest_key: str | None = None,
    conversation_id: int | None = None
) -> dict:
    """
    Store documents up to the plan's remaining document
    allowance. Checked before embedding, so files over
    the limit make no Gemini embedding calls.
    """

    usage = usage_service.get_usage_status(user, guest_key)

    remaining = usage["documents"]["remaining"]

    allowed = request.documents[:remaining]

    errors = {
        document.name: (
            f"Document limit reached for the "
            f"{usage['plan_name']} plan "
            f"({usage['documents']['limit']} per "
            f"{usage['period']}). "
            + (
                "Log in or create an account to upload more."
                if user is None
                else "Upgrade your plan to upload more."
            )
        )
        for document in request.documents[remaining:]
    }

    result = (
        add_documents(
            store_key,
            [document.model_dump() for document in allowed]
        )
        if allowed
        else {"documents": [], "chunks": 0, "errors": {}}
    )

    # Only successfully stored documents count.
    usage_service.record_document_uploads(
        len(result["documents"]),
        user=user,
        guest_key=guest_key,
        conversation_id=conversation_id
    )

    return {
        **result,
        "errors": {**result["errors"], **errors},
        "limit_reached": bool(errors)
    }

@app.post("/conversations/{conversation_id}/documents")
def upload_documents(
    conversation_id: int,
    request: DocumentsRequest,
    user: dict = Depends(get_current_user)
):

    get_owned_conversation(conversation_id, user["id"])

    return upload_within_limit(
        document_store_key(user["id"], conversation_id),
        request,
        user=user,
        conversation_id=conversation_id
    )


@app.get("/conversations/{conversation_id}/documents")
def get_documents(
    conversation_id: int,
    user: dict = Depends(get_current_user)
):

    get_owned_conversation(conversation_id, user["id"])

    return list_documents(
        document_store_key(user["id"], conversation_id)
    )


@app.delete("/conversations/{conversation_id}/documents")
def delete_documents(
    conversation_id: int,
    user: dict = Depends(get_current_user)
):

    get_owned_conversation(conversation_id, user["id"])

    clear_documents(
        document_store_key(user["id"], conversation_id)
    )

    return {"cleared": True}


@app.post("/conversations/import")
def import_guest_conversation(
    request: ImportRequest,
    user: dict = Depends(get_current_user),
    x_guest_token: str | None = Header(default=None)
):
    """
    Save a guest's conversation to the account they
    just logged into, and move the guest's uploaded
    documents into it (no re-embedding).
    """

    first_question = next(
        (m.content for m in request.messages if m.role == "user"),
        ""
    )

    conversation = new_conversation(
        user["id"],
        make_title(first_question)
    )

    for message in request.messages:

        metadata = message.model_dump(
            exclude={"role", "content"}
        )

        if message.role == "user":
            metadata = {"attachments": message.attachments}

        database.add_message(
            conversation["id"],
            message.role,
            message.content,
            metadata
        )

    if x_guest_token:

        move_documents(
            get_guest_key(x_guest_token),
            document_store_key(user["id"], conversation["id"])
        )

    return database.get_conversation(conversation["id"])


# =========================================================
# GUEST DOCUMENTS
# =========================================================

@app.post("/guest/documents")
def upload_guest_documents(
    request: DocumentsRequest,
    guest_key: str = Depends(get_guest_key)
):

    return upload_within_limit(
        guest_key,
        request,
        guest_key=guest_key
    )


@app.delete("/guest/documents")
def delete_guest_documents(
    guest_key: str = Depends(get_guest_key)
):

    clear_documents(guest_key)

    return {"cleared": True}


# =========================================================
# PLANS, ACCOUNT & USAGE
# =========================================================

@app.get("/plans")
def get_plans():
    """
    Plan limits and prices from configuration.
    """

    return public_plans()


@app.get("/account")
def get_account(
    user: dict | None = Depends(get_optional_user),
    x_guest_token: str | None = Header(default=None)
):
    """
    The caller's own account, plan and usage. Only ever
    about the authenticated user (or this guest session).
    """

    if user is None:

        return {
            "account": None,
            "usage": usage_service.get_usage_status(
                guest_key=get_guest_key(x_guest_token)
            )
        }

    return {
        "account": user,
        "usage": usage_service.get_usage_status(user)
    }


# =========================================================
# BILLING (payment provider not integrated yet)
# =========================================================

class CheckoutRequest(BaseModel):
    plan: str


@app.post("/billing/checkout")
def start_checkout(
    request: CheckoutRequest,
    user: dict = Depends(get_current_user)
):
    """
    Returns a provider checkout URL. The plan changes only
    later, when the provider's verified webhook arrives.
    """

    plan = PLANS.get(request.plan)

    if not plan or not plan["purchasable"]:
        raise HTTPException(
            status_code=400,
            detail="That plan can't be purchased."
        )

    # Avoid a second paid subscription for the same plan.
    if usage_service.get_user_plan(user["id"])[0] == request.plan:
        raise HTTPException(
            status_code=409,
            detail=f"You're already on the {plan['name']} plan."
        )

    try:

        provider = get_payment_provider()

        checkout_url = provider.create_checkout_session(
            user,
            request.plan
        )

    except PaymentNotConfigured as e:
        raise HTTPException(status_code=501, detail=str(e))

    except PaymentProviderError as e:
        raise HTTPException(status_code=502, detail=str(e))

    return {
        "checkout_url": checkout_url,
        "test_mode": getattr(provider, "test_mode", False)
    }


@app.post("/billing/portal")
def open_billing_portal(
    user: dict = Depends(get_current_user)
):

    try:

        provider = get_payment_provider()

        portal_url = provider.create_portal_session(user)

    except PaymentNotConfigured as e:
        raise HTTPException(status_code=501, detail=str(e))

    except PaymentProviderError as e:
        raise HTTPException(status_code=502, detail=str(e))

    return {"portal_url": portal_url}


@app.post("/billing/webhook")
async def billing_webhook(request: Request):
    """
    The only way a subscription changes: a payment
    provider event whose signature the provider module
    verifies with its secret. Duplicate and out-of-order
    deliveries are skipped.
    """

    try:

        provider = get_payment_provider()

        event = provider.verify_webhook(
            await request.body(),
            dict(request.headers)
        )

    except PaymentNotConfigured as e:
        raise HTTPException(status_code=501, detail=str(e))

    except InvalidWebhook:
        raise HTTPException(
            status_code=400,
            detail="Invalid webhook signature."
        )

    except (ValueError, KeyError, TypeError):
        raise HTTPException(
            status_code=400,
            detail="Malformed webhook."
        )

    # Verified but not relevant: acknowledge so the
    # provider doesn't keep retrying.
    if event is None:
        return {"received": True, "result": "ignored"}

    return {
        "received": True,
        "result": usage_service.process_subscription_event(event)
    }


# =========================================================
# ASK ENDPOINT
# =========================================================

@app.post("/ask")
def ask(
    request: QuestionRequest,
    user: dict | None = Depends(get_optional_user),
    x_guest_token: str | None = Header(default=None)
):
    """
    Wrapper around the existing question flow.

    Logged-in users: checks the conversation belongs to
    the user, points document lookups at that
    conversation's uploads, and saves both messages.

    Guests: answers without saving anything, using only
    the guest's own uploaded documents.

    Usage is checked BEFORE routing, so no Gemini call is
    made once the plan's limit is reached.
    """

    guest_key = None

    if user is None:

        if request.conversation_id is not None:
            raise HTTPException(
                status_code=401,
                detail="Please log in to continue."
            )

        # Guests are identified by their session token so
        # their usage can be counted.
        guest_key = get_guest_key(x_guest_token)

    # -----------------------------------------------------
    # Usage limit (backend is the source of truth)
    # -----------------------------------------------------

    usage = usage_service.get_usage_status(user, guest_key)

    if usage["limit_reached"]:

        return {
            **usage_service.limit_reached_response(usage),
            "conversation_id": request.conversation_id
        }

    request_id = usage_service.new_request_id()

    usage_service.start_token_tracking()

    if user is None:

        request.session_id = guest_key

        result = answer_question(request)

        record_answered_question(
            result, request, request_id, guest_key=guest_key
        )

        return {
            **result,
            "conversation_id": None,
            "usage": usage_service.get_usage_status(
                guest_key=guest_key
            )
        }

    if request.conversation_id is None:

        conversation = new_conversation(
            user["id"],
            make_title(request.question)
        )

    else:

        conversation = get_owned_conversation(
            request.conversation_id,
            user["id"]
        )

    request.session_id = document_store_key(
        user["id"],
        conversation["id"]
    )

    result = answer_question(request)

    question = request.question.strip()

    if question:

        database.add_message(
            conversation["id"],
            "user",
            question,
            {"attachments": request.attachments}
        )

        database.add_message(
            conversation["id"],
            "assistant",
            result.get("answer", ""),
            {
                "route": result.get("route"),
                "travel_sources": result.get(
                    "travel_sources",
                    result.get("sources", [])
                ),
                "places": result.get("places", []),
                "places_error": result.get("places_error")
            }
        )

    record_answered_question(
        result,
        request,
        request_id,
        user=user,
        conversation_id=conversation["id"]
    )

    return {
        **result,
        "conversation_id": conversation["id"],
        "usage": usage_service.get_usage_status(user)
    }


def record_answered_question(
    result: dict,
    request: QuestionRequest,
    request_id: str,
    **owner
):
    """
    One usage unit per answered question. Empty
    questions and failed answers (Gemini busy, quota)
    are not counted.
    """

    if request.question.strip() and result.get("route") != "error":

        usage_service.record_message(request_id, **owner)


NO_TRAVEL_INFO_MESSAGE = (
    "I don't have information about that topic "
    "in my travel documents."
)


def answer_knowledge_question(
    question: str,
    contextual_question: str
):
    """
    Answer from the travel documents when relevant ones
    exist. Otherwise, questions that explicitly ask what
    the documents say get a "no information" reply, and
    all others get a general answer.

    Returns (route, {"answer": ..., "sources": [...]}).
    The relevance check is the existing retrieval
    (embeddings only), so no extra Gemini call.
    """

    # Only the current question's own location (not one
    # from earlier messages) filters the documents.
    result = ask_rag(
        contextual_question,
        location=extract_location_from_text(question)
    )

    if result["sources"]:
        return "rag", result

    if refers_to_travel_documents(question):
        return "rag", {
            "answer": NO_TRAVEL_INFO_MESSAGE,
            "sources": []
        }

    return "general", ask_general(contextual_question)


def answer_question(request: QuestionRequest):

    question = request.question.strip()

    # -----------------------------------------------------
    # Validate empty question
    # -----------------------------------------------------

    if not question:
        return {
            "question": question,
            "route": "error",
            "answer": "Please enter a question.",
            "sources": [],
            "places": []
        }

    try:

        # =================================================
        # BUILD CONVERSATION CONTEXT
        # =================================================

        history_context = build_history_context(
            request.history
        )

        # =================================================
        # CREATE CONTEXTUAL QUESTION
        # =================================================

        contextual_question = question

        if history_context:
            contextual_question = f"""
Conversation history:

{history_context}

Current question:

{question}
"""

        # =================================================
        # UPLOADED DOCUMENTS
        # =================================================
        # If the session has uploaded documents with
        # content relevant to the question, answer from
        # them. Otherwise continue with normal routing.

        document_result = ask_documents(
            request.session_id,
            question,
            contextual_question
        )

        if document_result is not None:

            return {
                "question": question,
                "route": "documents",
                "answer": document_result["answer"],
                "sources": document_result["sources"],
                "places": []
            }

        # =================================================
        # ROUTER
        # =================================================

        route = route_question(
            question,
            request.history
        )

        print("\n==============================")
        print("NEW REQUEST")
        print("==============================")
        print("Question:", question)
        print("Route:", route)
        print("==============================\n")

        # =================================================
        # CONVERSATION
        # =================================================

        if route == "conversation":

            analysis = analyze_query(
                question,
                request.history
            )

            location = analysis.get(
                "location"
            )

            if location:
                return {
                    "question": question,
                    "route": "conversation",
                    "answer": (
                        f"📍 Got it! I'll remember that "
                        f"you're staying in {location}."
                    ),
                    "location": location,
                    "sources": [],
                    "places": []
                }

            return {
                "question": question,
                "route": "conversation",
                "answer": (
                    "Got it! I'll keep that in mind."
                ),
                "sources": [],
                "places": []
            }

        # =================================================
        # RAG
        # =================================================

        if route == "rag":

            route, result = answer_knowledge_question(
                question,
                contextual_question
            )

            return {
                "question": question,
                "route": route,
                "answer": result["answer"],
                "sources": result["sources"],
                "places": []
            }

        # =================================================
        # PLACES
        # =================================================

        if route == "places":

            places_result = search_places(
                question,
                history=request.history,
                max_results=5
            )

            print("\n========== PLACES RESULT ==========")
            print(places_result)
            print("===================================\n")

            places = places_result.get(
                "places",
                []
            )

            places_error = places_result.get(
                "error"
            )

            # ---------------------------------------------
            # Places found
            # ---------------------------------------------

            if places:

                answer = (
                    "Here are some nearby places "
                    "I found."
                )

            # ---------------------------------------------
            # No places / error
            # ---------------------------------------------

            elif places_error:

                answer = places_error

            else:

                answer = (
                    "I couldn't find any matching "
                    "places nearby."
                )

            return {
                "question": question,
                "route": "places",
                "answer": answer,
                "places": places,
                "places_error": places_error,
                "location": places_result.get(
                    "location"
                ),
                "coordinates": places_result.get(
                    "coordinates"
                ),
                "sources": []
            }

        # =================================================
        # RAG + PLACES
        # =================================================

        if route == "both":

            # ---------------------------------------------
            # Get travel information from RAG
            # (general answer if no document covers it)
            # ---------------------------------------------

            _, rag_result = answer_knowledge_question(
                question,
                contextual_question
            )

            # ---------------------------------------------
            # Get live places
            # ---------------------------------------------

            places_result = search_places(
                question,
                history=request.history,
                max_results=5
            )

            places = places_result.get(
                "places",
                []
            )

            places_error = places_result.get(
                "error"
            )

            return {
                "question": question,
                "route": "both",
                "answer": rag_result["answer"],
                "travel_sources": rag_result["sources"],
                "places": places,
                "places_error": places_error,
                "location": places_result.get(
                    "location"
                ),
                "coordinates": places_result.get(
                    "coordinates"
                )
            }

        # =================================================
        # FALLBACK
        # =================================================

        return {
            "question": question,
            "route": "unknown",
            "answer": (
                "I could not determine how to "
                "handle your question."
            ),
            "sources": [],
            "places": []
        }

    # =====================================================
    # GEMINI QUOTA ERROR
    # =====================================================

    except GoogleRateLimitError as e:

        print("\n========== GEMINI QUOTA ERROR ==========")
        print(e)
        print("========================================\n")

        # The free tier has a small per-day request limit;
        # a "retry in N seconds" hint doesn't apply to it.
        if "PerDay" in str(e):
            quota_hint = (
                "The daily Gemini free-tier request limit "
                "has been reached. It resets at midnight "
                "Pacific Time, or you can enable billing "
                "for the API key."
            )
        else:
            quota_hint = (
                "Too many requests in a short time. "
                "Please wait a minute and try again."
            )

        return {
            "question": request.question,
            "route": "error",
            "answer": (
                f"⚠️ Gemini API quota exceeded. {quota_hint}"
            ),
            "sources": [],
            "places": []
        }

    # =====================================================
    # GENERAL ERROR
    # =====================================================

    # =====================================================
    # GEMINI UNAVAILABLE (overloaded / timed out)
    # =====================================================

    # 5xx errors (503 high demand, 504 deadline) come
    # from the Google SDK's APIError family.
    except (
        ChatGoogleGenerativeAIError,
        GoogleSDKError,
        httpx.TimeoutException
    ) as e:

        print("\n========== GEMINI ERROR ==========")
        print(e)
        print("==================================\n")

        return {
            "question": request.question,
            "route": "error",
            "answer": (
                "⚠️ Gemini is experiencing high demand or "
                "didn't respond in time. This is usually "
                "temporary; please try again in a few minutes."
            ),
            "sources": [],
            "places": []
        }

    except Exception as e:

        print("\n========== UNEXPECTED ERROR ==========")
        print(e)
        print("======================================\n")

        return {
            "question": request.question,
            "route": "error",
            "answer": (
                "Sorry, something went wrong while "
                "processing your request. Please try again."
            ),
            "sources": [],
            "places": []
        }