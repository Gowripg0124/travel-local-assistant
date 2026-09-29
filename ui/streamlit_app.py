import base64
import secrets
import sys
from datetime import datetime, timedelta
from pathlib import Path
import os
import requests
import streamlit as st


# =========================================================
# PROJECT PATH
# =========================================================

PROJECT_ROOT = Path(__file__).resolve().parents[1]

if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))


# =========================================================
# CONFIGURATION
# =========================================================

API_URL = os.getenv(
    "API_URL",
    "http://127.0.0.1:8000/ask"
)

# Base URL for the other endpoints, derived from
# API_URL so one setting configures everything.
API_BASE_URL = API_URL.removesuffix("/ask")

# Seconds to wait for an answer. Gemini can take
# 20s+ per call, longer when it retries, so 60s
# was too short for document questions.
ASK_TIMEOUT = int(os.getenv("ASK_TIMEOUT", "180"))

SUPPORTED_FILE_TYPES = [
    "pdf",
    "docx",
    "txt",
    "csv",
    "md",
    "json"
]

DEFAULT_DOCUMENT_QUESTION = (
    "Please summarize the attached document(s)."
)

# Questions a guest can send before logging in.
GUEST_MESSAGE_LIMIT = 5

GUEST_LIMIT_MESSAGE = (
    "You've reached the guest message limit. "
    "Please log in or create an account to continue "
    "chatting and save your conversations."
)


# =========================================================
# PAGE CONFIGURATION
# =========================================================

st.set_page_config(
    page_title="Travel & Local Places Assistant",
    page_icon="🌍",
    layout="centered"
)


# =========================================================
# SESSION MEMORY
# =========================================================

# Cleared on login and logout, so each starts from a
# fresh chat and a fresh guest session.
AUTH_STATE_KEYS = [
    "auth_token",
    "user",
    "conversation_id",
    "messages",
    "documents",
    "guest_token",
    "guest_message_count",
    "auth_panel_open"
]

# Logged-in user and their bearer token.
if "auth_token" not in st.session_state:
    st.session_state.auth_token = None

# Guests: a random token that keys the guest's own
# uploaded documents on the server, and the number
# of questions they have sent.
if "guest_token" not in st.session_state:
    st.session_state.guest_token = secrets.token_urlsafe(32)

if "guest_message_count" not in st.session_state:
    st.session_state.guest_message_count = 0

# Whether the login / sign-up panel is shown.
if "auth_panel_open" not in st.session_state:
    st.session_state.auth_panel_open = False

if "user" not in st.session_state:
    st.session_state.user = None

# Current conversation (None = new chat, created
# on the first question).
if "conversation_id" not in st.session_state:
    st.session_state.conversation_id = None

if "messages" not in st.session_state:
    st.session_state.messages = []

# Names of documents attached to the current
# conversation (stored on the API server).
if "documents" not in st.session_state:
    st.session_state.documents = {}


# =========================================================
# API HELPERS
# =========================================================

class APIError(Exception):
    pass


def is_guest():

    return not st.session_state.auth_token


def guest_limit_reached():

    return (
        is_guest()
        and st.session_state.guest_message_count
        >= GUEST_MESSAGE_LIMIT
    )


def api_request(
    method,
    path,
    timeout=30,
    guest_token=None,
    **kwargs
):
    """
    Call the FastAPI backend with the user's token,
    or the guest token for guests.
    Raises APIError with a user-facing message.
    """

    headers = {}

    if st.session_state.auth_token:
        headers["Authorization"] = (
            f"Bearer {st.session_state.auth_token}"
        )

    guest_token = guest_token or (
        st.session_state.guest_token if is_guest() else None
    )

    if guest_token:
        headers["X-Guest-Token"] = guest_token

    try:

        response = requests.request(
            method,
            f"{API_BASE_URL}{path}",
            headers=headers,
            timeout=timeout,
            **kwargs
        )

    except requests.exceptions.Timeout:

        raise APIError(
            f"The server took longer than {timeout} seconds "
            "to respond. Gemini may be slow or retrying "
            "right now. Please try again in a moment."
        )

    except requests.exceptions.RequestException as e:

        raise APIError(
            f"Could not connect to the FastAPI server: {e}"
        )

    # Token expired or revoked: go back to login.
    if (
        response.status_code == 401
        and st.session_state.auth_token
        and not path.startswith("/auth/")
    ):
        clear_auth_state()

        st.warning(
            "Your session has expired. Please log in again."
        )

        st.stop()

    if not response.ok:

        try:
            detail = response.json().get("detail")
        except ValueError:
            detail = None

        raise APIError(
            detail
            or f"Request failed ({response.status_code})."
        )

    return response.json()


def clear_auth_state():

    for key in AUTH_STATE_KEYS:
        st.session_state.pop(key, None)


def start_new_chat():

    st.session_state.conversation_id = None
    st.session_state.messages = []
    st.session_state.documents = {}


def open_conversation(conversation_id):
    """
    Load a saved conversation's messages and
    documents from the backend.
    """

    st.session_state.messages = api_request(
        "GET",
        f"/conversations/{conversation_id}/messages"
    )

    st.session_state.documents = {
        name: True
        for name in api_request(
            "GET",
            f"/conversations/{conversation_id}/documents"
        )
    }

    st.session_state.conversation_id = conversation_id


# =========================================================
# HELPER FUNCTIONS
# =========================================================

def documents_path():
    """
    Guests upload to their guest session; logged-in
    users to the current conversation.
    """

    if is_guest():
        return "/guest/documents"

    return (
        f"/conversations/{st.session_state.conversation_id}"
        "/documents"
    )


def process_attachments(files):
    """
    Upload attached files to the API for the
    current conversation. The API extracts, chunks
    and embeds them. Returns the names of files
    that were attached successfully.
    """

    if not files:
        return []

    try:

        with st.spinner(
            "Reading documents..."
        ):

            result = api_request(
                "POST",
                documents_path(),
                json={
                    "documents": [
                        {
                            "name": uploaded_file.name,
                            "data_base64": base64.b64encode(
                                uploaded_file.getvalue()
                            ).decode("ascii")
                        }
                        for uploaded_file in files
                    ]
                },
                timeout=180
            )

    except APIError as e:

        st.error(
            f"Could not upload documents: {e}"
        )

        return []

    for name, error in result.get("errors", {}).items():

        st.warning(
            f"{name}: {error}"
        )

    attached_names = result.get(
        "documents",
        []
    )

    for name in attached_names:
        st.session_state.documents[name] = True

    return attached_names


def clear_uploaded_documents():
    """
    Remove the current conversation's documents
    from the API.
    """

    st.session_state.documents = {}

    if not is_guest() and st.session_state.conversation_id is None:
        return

    try:

        api_request(
            "DELETE",
            documents_path()
        )

    except APIError as e:

        st.error(str(e))


def group_conversations(conversations):
    """
    Group conversations into Today / Yesterday /
    Previous 7 days / Older by last update.
    """

    today = datetime.now().astimezone().date()

    groups = {
        "Today": [],
        "Yesterday": [],
        "Previous 7 days": [],
        "Older": []
    }

    for conversation in conversations:

        day = datetime.fromisoformat(
            conversation["updated_at"]
        ).astimezone().date()

        if day == today:
            groups["Today"].append(conversation)
        elif day == today - timedelta(days=1):
            groups["Yesterday"].append(conversation)
        elif day > today - timedelta(days=7):
            groups["Previous 7 days"].append(conversation)
        else:
            groups["Older"].append(conversation)

    return {
        label: items
        for label, items in groups.items()
        if items
    }


def open_auth_panel(mode):
    """
    Button callback: show the login / sign-up panel
    on the chosen form.
    """

    st.session_state.auth_panel_open = True
    st.session_state.auth_mode = mode


def complete_login(result):
    """
    Switch from guest to the logged-in user. The guest
    conversation (and its documents) is saved to the
    account as a new conversation.
    """

    guest_messages = st.session_state.messages
    guest_token = st.session_state.guest_token

    clear_auth_state()

    st.session_state.auth_token = result["token"]
    st.session_state.user = result["user"]

    start_new_chat()

    if not guest_messages:
        return

    try:

        conversation = api_request(
            "POST",
            "/conversations/import",
            guest_token=guest_token,
            json={"messages": guest_messages}
        )

        open_conversation(conversation["id"])

        st.session_state.auth_notice = (
            "Your guest conversation has been saved "
            "to your account."
        )

    except APIError:

        st.session_state.auth_notice = (
            "You're logged in. Your guest conversation "
            "couldn't be saved, so a new chat was started."
        )


def render_auth_panel():
    """
    Login / sign-up forms, shown when a guest chooses
    to log in or reaches the guest message limit.
    """

    with st.container(border=True):

        if guest_limit_reached():
            st.warning(GUEST_LIMIT_MESSAGE)
        else:
            st.markdown(
                "**Log in or create an account** to save "
                "your conversations and keep chatting."
            )

        mode = st.radio(
            "Account",
            ["Log in", "Sign up"],
            key="auth_mode",
            horizontal=True,
            label_visibility="collapsed"
        )

        action = "login" if mode == "Log in" else "signup"

        with st.form(f"{action}_form"):

            email = st.text_input(
                "Email",
                key=f"{action}_email"
            )

            password = st.text_input(
                "Password",
                type="password",
                key=f"{action}_password",
                help=(
                    "At least 8 characters."
                    if action == "signup"
                    else None
                )
            )

            submitted = st.form_submit_button(
                "Log in" if action == "login" else "Create account"
            )

        if not submitted:
            return

        if not email.strip() or not password:

            st.error(
                "Please enter your email and password."
            )

            return

        try:

            result = api_request(
                "POST",
                f"/auth/{action}",
                json={
                    "email": email,
                    "password": password
                }
            )

        except APIError as e:

            st.error(str(e))

            return

        complete_login(result)

        st.rerun()


def render_attachments(attachments):
    """
    Display attached file names as chips
    inside a user message.
    """

    if not attachments:
        return

    st.markdown(
        " ".join(
            f":gray-badge[📎 {name}]"
            for name in attachments
        )
    )


def render_places(places):
    """
    Display live places returned by the API.
    """

    if not places:
        return

    st.subheader("📍 Places Found")

    for index, place in enumerate(
        places,
        start=1
    ):

        name = place.get(
            "name",
            "Unknown"
        )

        address = place.get(
            "address",
            "Address unavailable"
        )

        place_type = place.get(
            "type",
            "place"
        )

        distance = place.get(
            "distance_meters"
        )

        latitude = place.get(
            "latitude"
        )

        longitude = place.get(
            "longitude"
        )

        with st.container():

            # ---------------------------------------------
            # PLACE NAME
            # ---------------------------------------------

            st.markdown(
                f"**{index}. {name}**"
            )

            # ---------------------------------------------
            # ADDRESS
            # ---------------------------------------------

            st.write(
                f"📍 {address}"
            )

            # ---------------------------------------------
            # TYPE
            # ---------------------------------------------

            st.write(
                f"🏷️ {place_type.title()}"
            )

            # ---------------------------------------------
            # DISTANCE
            # ---------------------------------------------

            if distance is not None:

                if distance < 1000:
                    distance_text = (
                        f"{round(distance)} m"
                    )
                else:
                    distance_text = (
                        f"{distance / 1000:.1f} km"
                    )

                st.write(
                    f"📏 {distance_text}"
                )

            # ---------------------------------------------
            # GOOGLE MAPS LINKS
            # ---------------------------------------------

            if (
                latitude is not None
                and longitude is not None
            ):

                maps_url = (
                    "https://www.google.com/maps/search/"
                    "?api=1"
                    f"&query={latitude},{longitude}"
                )

                directions_url = (
                    "https://www.google.com/maps/dir/"
                    "?api=1"
                    f"&destination={latitude},{longitude}"
                )

                col1, col2 = st.columns(2)

                with col1:

                    st.link_button(
                        "🗺️ View on Google Maps",
                        maps_url
                    )

                with col2:

                    st.link_button(
                        "🚗 Get Directions",
                        directions_url
                    )

            st.divider()


def render_sources(sources, route=None):
    """
    Display RAG or uploaded document sources.
    """

    if not sources:
        return

    st.subheader(
        "📎 Document Sources"
        if route == "documents"
        else "📚 Travel Sources"
    )

    for source in sources:

        st.write(
            f"📄 {source}"
        )


def render_message_metadata(message):
    """
    Display route, sources and places
    stored with an assistant message.
    """

    # ---------------------------------------------
    # ROUTE
    # ---------------------------------------------

    route = message.get("route")

    if route:

        st.caption(
            f"🔀 Query route: **{route}**"
        )

    # ---------------------------------------------
    # SOURCES
    # ---------------------------------------------

    travel_sources = message.get(
        "travel_sources",
        []
    )

    render_sources(
        travel_sources,
        route
    )

    # ---------------------------------------------
    # PLACES
    # ---------------------------------------------

    places = message.get(
        "places",
        []
    )

    render_places(
        places
    )

    # ---------------------------------------------
    # PLACES ERROR
    # ---------------------------------------------

    places_error = message.get(
        "places_error"
    )

    if places_error:

        st.warning(
            places_error
        )


# =========================================================
# HEADER
# =========================================================

st.title(
    "🌍 Travel & Local Places Assistant"
)

st.write(
    "Ask me about destinations, attractions, "
    "food, restaurants and nearby places."
)


# =========================================================
# DISPLAY PREVIOUS CONVERSATION
# =========================================================

for message in st.session_state.messages:

    role = message.get(
        "role",
        "assistant"
    )

    content = message.get(
        "content",
        ""
    )

    with st.chat_message(role):

        st.markdown(
            content
        )

        if role == "user":

            render_attachments(
                message.get("attachments", [])
            )

        # Only assistant messages
        # contain route/place metadata.
        if role == "assistant":

            render_message_metadata(
                message
            )


# =========================================================
# CHAT INPUT
# =========================================================

# accept_file="multiple" adds a 📎 attachment
# button inside the chat box (ChatGPT style).
prompt = st.chat_input(
    "Ask me about your trip...",
    accept_file="multiple",
    file_type=SUPPORTED_FILE_TYPES
)


# =========================================================
# GUEST MESSAGE LIMIT
# =========================================================
# Checked before anything is uploaded or sent, so a
# blocked message never reaches FastAPI.

if prompt and guest_limit_reached():

    st.session_state.auth_panel_open = True

    prompt = None


# =========================================================
# PROCESS QUESTION
# =========================================================

if prompt:

    question = prompt.text.strip()

    if not question and prompt.files:

        question = DEFAULT_DOCUMENT_QUESTION

    if not question:

        st.stop()

    # -----------------------------------------------------
    # CREATE CONVERSATION ON FIRST QUESTION
    # -----------------------------------------------------
    # Needed before uploading, since documents
    # belong to a conversation. Guests have no
    # saved conversations.

    if not is_guest() and st.session_state.conversation_id is None:

        try:

            conversation = api_request(
                "POST",
                "/conversations",
                json={"first_question": question}
            )

        except APIError as e:

            st.error(str(e))

            st.stop()

        st.session_state.conversation_id = conversation["id"]

    # -----------------------------------------------------
    # READ ATTACHMENTS
    # -----------------------------------------------------

    attachments = process_attachments(
        prompt.files
    )

    # -----------------------------------------------------
    # SHOW USER MESSAGE
    # -----------------------------------------------------

    with st.chat_message("user"):

        st.markdown(
            question
        )

        render_attachments(
            attachments
        )

    # -----------------------------------------------------
    # SAVE USER MESSAGE
    # -----------------------------------------------------

    st.session_state.messages.append(
        {
            "role": "user",
            "content": question,
            "attachments": attachments
        }
    )

    # -----------------------------------------------------
    # CALL FASTAPI
    # -----------------------------------------------------

    # The API saves both the question and the
    # answer to this conversation.
    try:

        with st.spinner(
            "Thinking..."
        ):

            result = api_request(
                "POST",
                "/ask",
                json={
                    "question": question,
                    "history": st.session_state.messages,
                    "conversation_id": st.session_state.conversation_id,
                    "attachments": attachments
                },
                timeout=ASK_TIMEOUT
            )

    except APIError as e:

        # Not saved by the API, so drop it here too.
        st.session_state.messages.pop()

        st.error(
            "Could not get an answer from the FastAPI server."
        )

        st.code(
            str(e)
        )

        st.stop()

    # -----------------------------------------------------
    # GET ANSWER
    # -----------------------------------------------------

    answer = result.get(
        "answer",
        "Sorry, I could not generate an answer."
    )

    # -----------------------------------------------------
    # GET RESPONSE DATA
    # -----------------------------------------------------

    route = result.get(
        "route",
        "unknown"
    )

    travel_sources = result.get(
        "travel_sources",
        result.get(
            "sources",
            []
        )
    )

    places = result.get(
        "places",
        []
    )

    places_error = result.get(
        "places_error"
    )

    # -----------------------------------------------------
    # DISPLAY ASSISTANT RESPONSE
    # -----------------------------------------------------

    with st.chat_message("assistant"):

        st.markdown(
            answer
        )

        st.caption(
            f"🔀 Query route: **{route}**"
        )

        render_sources(
            travel_sources,
            route
        )

        render_places(
            places
        )

        if places_error:

            st.warning(
                places_error
            )

    # -----------------------------------------------------
    # SAVE COMPLETE ASSISTANT MESSAGE
    # -----------------------------------------------------

    st.session_state.messages.append(
        {
            "role": "assistant",
            "content": answer,
            "route": route,
            "travel_sources": travel_sources,
            "places": places,
            "places_error": places_error
        }
    )

    # -----------------------------------------------------
    # COUNT GUEST QUESTION
    # -----------------------------------------------------
    # One per question actually answered; uploads and
    # assistant replies are not counted, and neither
    # are failures (e.g. Gemini busy or quota errors).

    if is_guest() and route != "error":

        st.session_state.guest_message_count += 1


# =========================================================
# LOGIN / SIGN-UP PANEL
# =========================================================

if "auth_notice" in st.session_state:

    st.success(
        st.session_state.pop("auth_notice")
    )

if is_guest() and (
    st.session_state.auth_panel_open
    or guest_limit_reached()
):

    render_auth_panel()


# =========================================================
# SIDEBAR
# =========================================================

with st.sidebar:

    st.header(
        "🌍 Travel Assistant"
    )

    # -----------------------------------------------------
    # GUEST: USAGE + LOGIN
    # -----------------------------------------------------

    if is_guest():

        st.caption(
            f"💬 Guest messages: "
            f"{st.session_state.guest_message_count}"
            f" / {GUEST_MESSAGE_LIMIT}"
        )

        st.caption(
            "Log in to save your conversations "
            "and keep chatting."
        )

        login_column, signup_column = st.columns(2)

        with login_column:

            st.button(
                "Login",
                width="stretch",
                on_click=open_auth_panel,
                args=("Log in",)
            )

        with signup_column:

            st.button(
                "Sign Up",
                type="primary",
                width="stretch",
                on_click=open_auth_panel,
                args=("Sign up",)
            )

        if st.session_state.messages and st.button(
            "🗑️ Clear Conversation",
            width="stretch"
        ):

            # Doesn't reset the guest message count.
            st.session_state.messages = []

            clear_uploaded_documents()

            st.rerun()

    # -----------------------------------------------------
    # LOGGED IN: NEW CHAT + HISTORY
    # -----------------------------------------------------

    else:

        # -----------------------------------------------------
        # NEW CHAT
        # -----------------------------------------------------

        if st.button(
            "➕ New Chat",
            width="stretch"
        ):

            start_new_chat()

            st.rerun()

        # -----------------------------------------------------
        # CHAT HISTORY
        # -----------------------------------------------------

        try:

            conversations = api_request(
                "GET",
                "/conversations"
            )

        except APIError as e:

            st.error(str(e))

            conversations = []

        if not conversations:

            st.caption(
                "Your conversations will appear here."
            )

        for label, items in group_conversations(
            conversations
        ).items():

            st.caption(label)

            for conversation in items:

                is_current = (
                    conversation["id"]
                    == st.session_state.conversation_id
                )

                if st.button(
                    conversation["title"],
                    key=f"conversation_{conversation['id']}",
                    type="primary" if is_current else "secondary",
                    width="stretch"
                ) and not is_current:

                    try:

                        open_conversation(
                            conversation["id"]
                        )

                    except APIError as e:

                        st.error(str(e))

                    else:

                        st.rerun()

    st.divider()

    with st.expander("ℹ️ About & example questions"):

        st.write(
            "This assistant combines:"
        )

        st.write(
            "📚 RAG\n\n"
            "📍 Places Search\n\n"
            "🤖 Gemini\n\n"
            "💬 Conversation Memory"
        )

        st.write(
            "• What are popular places to visit in Ooty?"
        )

        st.write(
            "• What is Coimbatore famous for?"
        )

        st.write(
            "• Find restaurants near RS Puram"
        )

        st.write(
            "• Find cafes near Gandhipuram"
        )

        st.write(
            "• I'm visiting Coimbatore for 2 days. "
            "What should I visit and where can I eat?"
        )

    st.divider()

    # -----------------------------------------------------
    # ATTACHED DOCUMENTS
    # -----------------------------------------------------

    st.subheader(
        "📎 Attached documents"
    )

    if st.session_state.documents:

        for name in st.session_state.documents:

            st.write(
                f"📄 {name}"
            )

        if st.button(
            "Remove all documents"
        ):

            clear_uploaded_documents()

            st.rerun()

    else:

        st.caption(
            "Use the 📎 button in the chat box "
            "to attach one or more documents."
        )

    st.divider()

    # -----------------------------------------------------
    # DELETE CHAT
    # -----------------------------------------------------
    # Replaces "Clear Conversation": New Chat starts a
    # fresh chat, and this removes the saved one.

    if st.session_state.conversation_id is not None:

        if st.button(
            "🗑️ Delete Conversation"
        ):

            try:

                api_request(
                    "DELETE",
                    f"/conversations/{st.session_state.conversation_id}"
                )

            except APIError as e:

                st.error(str(e))

            else:

                start_new_chat()

                st.rerun()

    # -----------------------------------------------------
    # ACCOUNT
    # -----------------------------------------------------

    if not is_guest():

        st.divider()

        st.caption(
            f"👤 {st.session_state.user['email']}"
        )

        if st.button(
            "🚪 Logout"
        ):

            try:
                api_request("POST", "/auth/logout")
            except APIError:
                pass

            # Back to a fresh guest session.
            clear_auth_state()

            st.rerun()
