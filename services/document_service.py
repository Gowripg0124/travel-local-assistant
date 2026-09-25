import os
import uuid

from dotenv import load_dotenv
from langchain_chroma import Chroma
from langchain_core.documents import Document
from langchain_google_genai import GoogleGenerativeAIEmbeddings
from langchain_text_splitters import RecursiveCharacterTextSplitter

from rag.rag_service import llm, extract_text
from rag.retriever import (
    SimilarityThresholdRetriever,
    RELEVANCE_THRESHOLD
)

load_dotenv()

MOCK_LLM = os.getenv(
    "MOCK_LLM",
    "false"
).lower() == "true"

NOT_FOUND_MESSAGE = (
    "I could not find that information "
    "in the uploaded documents."
)


# Returned by Gemini when the question is not about
# the uploaded documents (e.g. general travel or
# nearby places), so normal routing handles it.
NOT_RELEVANT_MARKER = "NOT_RELEVANT"


DOCUMENT_PROMPT = """
You are a helpful assistant answering questions
about documents the user has uploaded.
Each context section below is an excerpt from an
uploaded document and starts with its source filename.

First decide whether the question is about the
user's uploaded documents (their trip, bookings,
plans, notes or anything the documents cover).

- If the question is clearly NOT about the uploaded
  documents (for example a general travel question
  or a request to find nearby places), reply with
  exactly: {not_relevant}

- If the question is about the uploaded documents
  but the answer is not in the context, reply with
  exactly: "{not_found}"

- Otherwise, answer using ONLY the information in
  the context and mention the source filename(s)
  the answer came from.

Do not invent information.

Context:
{context}

Question:
{question}

Answer:
"""


# =========================================================
# SESSION DOCUMENT STORES
# =========================================================

# One in-memory Chroma collection per chat session.
# Uploaded documents live only while the API is running.
_session_stores: dict[str, Chroma] = {}

_embeddings = None


def _get_embeddings():

    global _embeddings

    if _embeddings is None:

        _embeddings = GoogleGenerativeAIEmbeddings(
            model="gemini-embedding-2"
        )

    return _embeddings


def _get_store(session_id: str) -> Chroma:

    if session_id not in _session_stores:

        _session_stores[session_id] = Chroma(
            collection_name=(
                f"uploads_{uuid.uuid4().hex}"
            ),
            embedding_function=_get_embeddings()
        )

    return _session_stores[session_id]


def has_documents(session_id: str | None) -> bool:

    return bool(
        session_id
        and session_id in _session_stores
    )


# =========================================================
# ADD / CLEAR DOCUMENTS
# =========================================================

def add_documents(
    session_id: str,
    documents: list[dict]
) -> int:
    """
    Split, embed and store uploaded documents.
    documents: [{"name": ..., "content": ...}]
    Returns the number of chunks stored.
    """

    store = _get_store(session_id)

    splitter = RecursiveCharacterTextSplitter(
        chunk_size=500,
        chunk_overlap=100
    )

    chunks = []

    for document in documents:

        name = document["name"]

        # Re-uploading a file replaces its old chunks.
        existing = store.get(
            where={"source": name}
        )

        if existing["ids"]:
            store.delete(
                ids=existing["ids"]
            )

        chunks.extend(
            splitter.split_documents(
                [
                    Document(
                        page_content=document["content"],
                        metadata={"source": name}
                    )
                ]
            )
        )

    if chunks:
        store.add_documents(chunks)

    return len(chunks)


def clear_documents(session_id: str):

    store = _session_stores.pop(
        session_id,
        None
    )

    if store is not None:
        store.delete_collection()


# =========================================================
# ASK UPLOADED DOCUMENTS
# =========================================================

def ask_documents(
    session_id: str,
    question: str,
    contextual_question: str | None = None
):
    """
    Answer a question from the session's uploaded
    documents. Returns None when the question is
    not about the uploaded documents.
    """

    if not has_documents(session_id):
        return None

    # Relevance scores don't reliably separate document
    # questions from other questions, so take the top
    # chunks and let Gemini judge relevance. The mock
    # LLM can't judge, so it keeps the score threshold.
    retriever = SimilarityThresholdRetriever(
        vectorstore=_session_stores[session_id],
        k=4,
        threshold=(
            RELEVANCE_THRESHOLD if MOCK_LLM
            else float("-inf")
        )
    )

    documents = retriever.invoke(
        question
    )

    if not documents:
        return None

    context = "\n\n".join(
        f"[Source: {document.metadata.get('source', 'unknown')}]\n"
        f"{document.page_content}"
        for document in documents
    )

    sources = list(
        dict.fromkeys(
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

        return {
            "answer": (
                "Based on the uploaded documents:\n\n"
                + context
            ),
            "sources": sources
        }

    # =====================================================
    # REAL GEMINI
    # =====================================================

    prompt = DOCUMENT_PROMPT.format(
        not_relevant=NOT_RELEVANT_MARKER,
        not_found=NOT_FOUND_MESSAGE,
        context=context,
        question=contextual_question or question
    )

    answer = extract_text(
        llm.invoke(prompt)
    ).strip()

    if answer.strip('"').startswith(NOT_RELEVANT_MARKER):
        return None

    # Don't cite sources for a "not found" answer.
    if NOT_FOUND_MESSAGE.lower() in answer.lower():
        sources = []

    # Prefer the files Gemini actually cited.
    cited = [
        source
        for source in sources
        if source in answer
    ]

    if cited:
        sources = cited

    return {
        "answer": answer,
        "sources": sources
    }
