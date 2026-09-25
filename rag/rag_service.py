import os

from dotenv import load_dotenv
from langchain_google_genai import ChatGoogleGenerativeAI

from rag.retriever import get_retriever
from rag.prompt import SYSTEM_PROMPT

load_dotenv()

MOCK_LLM = os.getenv(
    "MOCK_LLM",
    "false"
).lower() == "true"


llm = ChatGoogleGenerativeAI(
    model="gemini-3.6-flash"
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


def ask_rag(question: str):

    retriever = get_retriever()

    documents = retriever.invoke(
        question
    )

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