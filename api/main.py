from fastapi import FastAPI
from pydantic import BaseModel, Field
from langchain_google_genai.chat_models import GoogleRateLimitError

from rag.rag_service import ask_rag
from services.router import route_question
from services.places_service import search_places
from services.query_analyzer import analyze_query
from services.document_service import (
    add_documents,
    clear_documents,
    ask_documents
)


# =========================================================
# FASTAPI APP
# =========================================================

app = FastAPI(
    title="Travel & Local Places Assistant",
    description="RAG and location-aware travel assistant",
    version="1.0.0"
)


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
    session_id: str | None = None


class UploadedDocument(BaseModel):
    name: str
    data_base64: str


class DocumentsRequest(BaseModel):
    session_id: str
    documents: list[UploadedDocument]


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
# UPLOADED DOCUMENTS
# =========================================================

@app.post("/documents")
def upload_documents(request: DocumentsRequest):

    return add_documents(
        request.session_id,
        [
            document.model_dump()
            for document in request.documents
        ]
    )


@app.delete("/documents/{session_id}")
def delete_documents(session_id: str):

    clear_documents(session_id)

    return {"cleared": True}


# =========================================================
# ASK ENDPOINT
# =========================================================

@app.post("/ask")
def ask(request: QuestionRequest):

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

            result = ask_rag(
                contextual_question
            )

            return {
                "question": question,
                "route": "rag",
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
            # ---------------------------------------------

            rag_result = ask_rag(
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