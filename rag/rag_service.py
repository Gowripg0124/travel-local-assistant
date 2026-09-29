import os

from dotenv import load_dotenv
from langchain_google_genai import ChatGoogleGenerativeAI

from rag.retriever import get_retriever
from rag.prompt import GENERAL_PROMPT, SYSTEM_PROMPT

load_dotenv()

MOCK_LLM = os.getenv(
    "MOCK_LLM",
    "false"
).lower() == "true"


llm = ChatGoogleGenerativeAI(
    model="gemini-3.5-flash"
)


def extract_text(response):

    if isinstance(response.content, str):
        return response.content

    text_parts = []

    for block in response.content:

        if (
            isinstance(block, dict)
            and block.get("type") == "text"
        ):
            text_parts.append(
                block.get("text", "")
            )

    return "\n".join(text_parts)


def ask_rag(question: str, location: str | None = None):
    """
    location: when the question names a place, keep only
    retrieved documents that mention it, so a guide for
    another city isn't used as the answer.
    """

    retriever = get_retriever()

    documents = retriever.invoke(
        question
    )

    if location:

        # "RS Puram, Coimbatore" matches either part.
        names = [
            part.strip().lower()
            for part in location.split(",")
            if part.strip()
        ]

        documents = [
            document
            for document in documents
            if any(
                name in document.page_content.lower()
                for name in names
            )
        ]

    if not documents:

        return {
            "answer": (
                "I could not find relevant "
                "information in the travel documents."
            ),
            "sources": []
        }

    # Build context FIRST
    context = "\n\n".join(
        document.page_content
        for document in documents
    )

    sources = list(
        set(
            document.metadata.get(
                "source",
                "unknown"
            )
            for document in documents
        )
    )

    # =====================================================
    # MOCK LLM
    # =====================================================

    if MOCK_LLM:

        answer = (
            "Based on the retrieved travel documents:\n\n"
            + context
        )

        return {
            "answer": answer,
            "sources": sources
        }

    # =====================================================
    # REAL GEMINI
    # =====================================================

    prompt = SYSTEM_PROMPT.format(
        context=context,
        question=question
    )

    response = llm.invoke(
        prompt
    )

    answer = extract_text(
        response
    )

    return {
        "answer": answer,
        "sources": sources
    }

def ask_general(question: str):
    """
    Answer a general question that the travel
    documents don't cover, without the
    context-only restriction of the RAG prompt.

    Always calls Gemini, even when MOCK_LLM is on (like
    uploaded-document questions): there is no retrieved
    text to show instead, so a mock can't answer.
    """

    # Same limits as document questions, so a busy
    # Gemini fails with a clear error instead of
    # outlasting the UI's wait.
    response = llm.invoke(
        GENERAL_PROMPT.format(
            question=question
        ),
        timeout=50,
        max_retries=3
    )

    return {
        "answer": extract_text(response),
        "sources": []
    }
