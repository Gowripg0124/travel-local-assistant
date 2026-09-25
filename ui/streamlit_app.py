import io
import sys
import uuid
import zipfile
from pathlib import Path
from xml.etree import ElementTree

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

API_URL = "http://127.0.0.1:8000/ask"

DOCUMENTS_URL = "http://127.0.0.1:8000/documents"

SUPPORTED_FILE_TYPES = [
    "pdf",
    "docx",
    "txt",
    "csv",
    "md",
    "json"
]

# Limit extracted text per document so the
# API payload stays reasonably small.
MAX_DOCUMENT_CHARS = 20000

DEFAULT_DOCUMENT_QUESTION = (
    "Please summarize the attached document(s)."
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

if "messages" not in st.session_state:
    st.session_state.messages = []

# Documents attached during the conversation,
# keyed by file name -> extracted text.
if "documents" not in st.session_state:
    st.session_state.documents = {}

# Identifies this chat session's uploaded
# documents on the API server.
if "session_id" not in st.session_state:
    st.session_state.session_id = uuid.uuid4().hex


# =========================================================
# HELPER FUNCTIONS
# =========================================================

def extract_document_text(uploaded_file):
    """
    Extract plain text from an uploaded file.
    """

    extension = Path(
        uploaded_file.name
    ).suffix.lower().lstrip(".")

    data = uploaded_file.getvalue()

    # ---------------------------------------------
    # PDF
    # ---------------------------------------------

    if extension == "pdf":

        from pypdf import PdfReader

        reader = PdfReader(
            io.BytesIO(data)
        )

        text = "\n".join(
            page.extract_text() or ""
            for page in reader.pages
        )

    # ---------------------------------------------
    # DOCX (read word/document.xml directly)
    # ---------------------------------------------

    elif extension == "docx":

        namespace = (
            "{http://schemas.openxmlformats.org/"
            "wordprocessingml/2006/main}"
        )

        with zipfile.ZipFile(io.BytesIO(data)) as archive:

            root = ElementTree.fromstring(
                archive.read("word/document.xml")
            )

        paragraphs = []

        for paragraph in root.iter(f"{namespace}p"):

            paragraphs.append(
                "".join(
                    node.text or ""
                    for node in paragraph.iter(f"{namespace}t")
                )
            )

        text = "\n".join(paragraphs)

    # ---------------------------------------------
    # TXT / CSV / MD / JSON
    # ---------------------------------------------

    else:

        text = data.decode(
            "utf-8",
            errors="replace"
        )

    return text.strip()[:MAX_DOCUMENT_CHARS]


def process_attachments(files):
    """
    Extract text from attached files and upload
    them to the API for this chat session.
    Returns the names of files that were
    attached successfully.
    """

    extracted = {}

    for uploaded_file in files:

        try:

            text = extract_document_text(
                uploaded_file
            )

        except Exception as e:

            st.warning(
                f"Could not read {uploaded_file.name}: {e}"
            )

            continue

        if not text:

            st.warning(
                f"No readable text found in {uploaded_file.name}."
            )

            continue

        extracted[
            uploaded_file.name
        ] = text

    if not extracted:
        return []

    try:

        with st.spinner(
            "Reading documents..."
        ):

            response = requests.post(
                DOCUMENTS_URL,
                json={
                    "session_id": st.session_state.session_id,
                    "documents": [
                        {
                            "name": name,
                            "content": text
                        }
                        for name, text in extracted.items()
                    ]
                },
                timeout=120
            )

            response.raise_for_status()

    except requests.exceptions.RequestException as e:

        st.error(
            f"Could not upload documents: {e}"
        )

        return []

    st.session_state.documents.update(
        extracted
    )

    return list(extracted)


def clear_uploaded_documents():
    """
    Remove this session's documents from the API.
    """

    st.session_state.documents = {}

    try:

        requests.delete(
            f"{DOCUMENTS_URL}/{st.session_state.session_id}",
            timeout=10
        )

    except requests.exceptions.RequestException:
        pass


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
# PROCESS QUESTION
# =========================================================

if prompt:

    # -----------------------------------------------------
    # READ ATTACHMENTS
    # -----------------------------------------------------

    attachments = process_attachments(
        prompt.files
    )

    question = prompt.text.strip()

    if not question and attachments:

        question = DEFAULT_DOCUMENT_QUESTION

    if not question:

        st.stop()

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

    try:

        with st.spinner(
            "Thinking..."
        ):

            response = requests.post(
                API_URL,
                json={
                    "question": question,
                    "history": st.session_state.messages,
                    "session_id": st.session_state.session_id
                },
                timeout=60
            )

            response.raise_for_status()

            result = response.json()

    except requests.exceptions.RequestException as e:

        st.error(
            "Could not connect to the FastAPI server."
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


# =========================================================
# SIDEBAR
# =========================================================

with st.sidebar:

    st.header(
        "🌍 Travel Assistant"
    )

    st.write(
        "This assistant combines:"
    )

    st.write(
        "📚 RAG\n\n"
        "📍 Places Search\n\n"
        "🤖 Gemini\n\n"
        "💬 Conversation Memory"
    )

    st.divider()

    st.subheader(
        "Example questions"
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
    # CLEAR CHAT
    # -----------------------------------------------------

    if st.button(
        "🗑️ Clear Conversation"
    ):

        st.session_state.messages = []

        clear_uploaded_documents()

        st.rerun()