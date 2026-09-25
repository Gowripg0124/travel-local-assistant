import base64
import io
import re
import zipfile
from pathlib import Path
from xml.etree import ElementTree

from dotenv import load_dotenv
from langchain_chroma import Chroma
from langchain_core.documents import Document
from langchain_google_genai import GoogleGenerativeAIEmbeddings
from langchain_text_splitters import RecursiveCharacterTextSplitter

from rag.rag_service import llm, extract_text
from services.query_analyzer import detect_intents

load_dotenv()


# =========================================================
# CONFIGURATION
# =========================================================

# Kept separate from the built-in travel vectorstore
# so the two knowledge sources never mix. Persisted
# so uploads survive `uvicorn --reload` restarts.
UPLOADS_VECTORSTORE_DIR = "vectorstore_uploads"

SUPPORTED_FILE_TYPES = {
    "pdf",
    "docx",
    "txt",
    "csv",
    "md",
    "json"
}

# If all uploaded text in a session fits within this
# many characters, send every chunk to Gemini so it can
# analyse whole documents. Otherwise retrieve the top
# chunks from each document.
FULL_CONTEXT_CHARS = 40000

CHUNKS_PER_DOCUMENT = 4

NOT_FOUND_MESSAGE = (
    "I could not find that information "
    "in the uploaded documents."
)

# Returned by Gemini when the question is not about
# the uploaded documents, so normal routing handles it.
NOT_RELEVANT_MARKER = "NOT_RELEVANT"

# Words that show the user is referring to the uploads.
DOCUMENT_REFERENCE_WORDS = {
    "document", "documents", "doc", "docs",
    "pdf", "pdfs", "docx", "csv", "json",
    "file", "files", "attachment", "attachments",
    "attached", "upload", "uploaded", "uploads",
    "spreadsheet", "sheet"
}


DOCUMENT_PROMPT = """
You are a document analysis assistant. The user has
uploaded one or more documents, which can be about any
topic (for example a score report, resume, financial
report, project plan, notes or data file), and is asking
a question about them.

Each context section below is an excerpt from an uploaded
document and starts with its source filename (and page
number where available).

HOW TO RESPOND
{relevance_instruction}
- If the document does not contain the information needed
  to answer, reply with exactly: "{not_found}"
  Questions asking for analysis, feedback, priorities or
  improvement advice ARE answerable whenever the documents
  contain material to analyse.

- Never copy or dump the document content back to the
  user. Answer the question by analysing, synthesising and
  organising the relevant information.

- Adapt the answer to the question:
  * Factual question: answer directly and concisely.
  * Summary / key points / key issues: give the most
    important points, grouped logically.
  * Analysis, "where can I improve", "what should I focus
    on", feedback or review: work out the document's type
    and evaluate it accordingly, then:
      1. Identify the weakest or most problematic areas and
         prioritise them, most important first. Include the
         exact scores, percentages, figures or evidence the
         document gives for each.
      2. Explain briefly what each area is about, using the
         document's own description where it has one.
      3. Give practical, specific suggestions for improving
         each area.
      4. Where useful, list the strong areas separately.
      5. Finish with a short overall plan or next steps.
  * Comparison across documents: compare them explicitly
    and name the document each point comes from.

- Grounding rules:
  * Every fact, score, name, figure and claim must come
    from the context. Never invent or estimate values, and
    never add topics or items the document doesn't mention.
  * Your suggestions and advice may use general knowledge,
    but only about the topics and items that are actually
    in the document.
  * If the context looks incomplete for the question, say
    which part you could not find.

- Formatting: use Markdown headings, bullet points and bold
  text for readability. Keep it focused, not verbose.

- Name the source filename(s) your answer came from.

Context:
{context}

Question:
{question}

Answer:
"""

RELEVANCE_INSTRUCTION = f"""
- If the question is clearly NOT about the uploaded
  documents (for example a general travel question or a
  request to find nearby places), reply with exactly:
  {NOT_RELEVANT_MARKER}
"""


# =========================================================
# TEXT EXTRACTION
# =========================================================

def extract_pages(
    filename: str,
    data: bytes
) -> list[tuple[int | None, str]]:
    """
    Extract text from a file.
    Returns [(page_number or None, text)].
    """

    extension = Path(filename).suffix.lower().lstrip(".")

    if extension not in SUPPORTED_FILE_TYPES:
        raise ValueError(
            f"Unsupported file type: .{extension}"
        )

    # ---------------------------------------------
    # PDF (one entry per page)
    # ---------------------------------------------

    if extension == "pdf":

        from pypdf import PdfReader

        reader = PdfReader(io.BytesIO(data))

        return [
            (number, page.extract_text() or "")
            for number, page in enumerate(
                reader.pages,
                start=1
            )
        ]

    # ---------------------------------------------
    # DOCX (read word/document.xml directly)
    # ---------------------------------------------

    if extension == "docx":

        namespace = (
            "{http://schemas.openxmlformats.org/"
            "wordprocessingml/2006/main}"
        )

        with zipfile.ZipFile(io.BytesIO(data)) as archive:

            root = ElementTree.fromstring(
                archive.read("word/document.xml")
            )

        paragraphs = [
            "".join(
                node.text or ""
                for node in paragraph.iter(f"{namespace}t")
            )
            for paragraph in root.iter(f"{namespace}p")
        ]

        return [(None, "\n".join(paragraphs))]

    # ---------------------------------------------
    # TXT / CSV / MD / JSON
    # ---------------------------------------------

    return [(None, data.decode("utf-8", errors="replace"))]


# =========================================================
# SESSION DOCUMENT STORES
# =========================================================

_embeddings = None

_session_stores: dict[str, Chroma] = {}

# Sessions whose next question should go to the uploaded
# documents because a file was just uploaded.
_pending_upload: set[str] = set()


def _get_embeddings():

    global _embeddings

    if _embeddings is None:

        _embeddings = GoogleGenerativeAIEmbeddings(
            model="gemini-embedding-2"
        )

    return _embeddings


def _get_store(session_id: str) -> Chroma:

    if session_id not in _session_stores:

        safe_id = re.sub(
            r"[^A-Za-z0-9_-]",
            "",
            session_id
        )[:50]

        _session_stores[session_id] = Chroma(
            collection_name=f"uploads_{safe_id}",
            embedding_function=_get_embeddings(),
            persist_directory=UPLOADS_VECTORSTORE_DIR
        )

    return _session_stores[session_id]


def _all_chunks(store: Chroma) -> list[Document]:

    data = store.get(
        include=["documents", "metadatas"]
    )

    return [
        Document(
            page_content=text,
            metadata=metadata or {}
        )
        for text, metadata in zip(
            data["documents"],
            data["metadatas"]
        )
    ]


def has_documents(session_id: str | None) -> bool:

    if not session_id:
        return False

    return _get_store(session_id)._collection.count() > 0


# =========================================================
# ADD / CLEAR DOCUMENTS
# =========================================================

def add_documents(
    session_id: str,
    files: list[dict]
) -> dict:
    """
    Extract, split, embed and store uploaded files.
    files: [{"name": ..., "data_base64": ...}]
    """

    store = _get_store(session_id)

    splitter = RecursiveCharacterTextSplitter(
        chunk_size=500,
        chunk_overlap=100
    )

    added = []
    errors = {}
    chunks = []

    for file in files:

        name = file["name"]

        try:

            pages = extract_pages(
                name,
                base64.b64decode(file["data_base64"])
            )

        except Exception as e:

            errors[name] = f"Could not read file: {e}"
            continue

        file_chunks = []

        for page, text in pages:

            if not text.strip():
                continue

            metadata = {
                "source": name,
                "file_type": Path(name).suffix.lower().lstrip(".")
            }

            if page is not None:
                metadata["page"] = page

            file_chunks.extend(
                splitter.split_documents(
                    [Document(page_content=text, metadata=metadata)]
                )
            )

        if not file_chunks:

            errors[name] = (
                "No readable text found "
                "(scanned or image-only files are not supported)."
            )
            continue

        for index, chunk in enumerate(file_chunks):
            chunk.metadata["chunk"] = index

        # Re-uploading a file replaces its old chunks.
        existing = store.get(where={"source": name})

        if existing["ids"]:
            store.delete(ids=existing["ids"])

        chunks.extend(file_chunks)
        added.append(name)

    if chunks:

        store.add_documents(chunks)

        _pending_upload.add(session_id)

    return {
        "documents": added,
        "chunks": len(chunks),
        "errors": errors
    }


def clear_documents(session_id: str):

    store = _get_store(session_id)

    store.delete_collection()

    _session_stores.pop(session_id, None)
    _pending_upload.discard(session_id)


# =========================================================
# RETRIEVAL
# =========================================================

def _refers_to_documents(
    question: str,
    sources: list[str]
) -> bool:
    """
    True when the question explicitly mentions the
    uploads, e.g. "this document" or a filename.
    """

    question_lower = question.lower()

    words = set(re.findall(r"[a-z0-9]+", question_lower))

    if words & DOCUMENT_REFERENCE_WORDS:
        return True

    for source in sources:

        stem = re.sub(
            r"[-_.]+",
            " ",
            Path(source).stem.lower()
        ).strip()

        if (
            source.lower() in question_lower
            or (stem and stem in question_lower)
        ):
            return True

    return False


def _retrieve(
    store: Chroma,
    question: str,
    sources: list[str]
) -> list[tuple[Document, float]]:
    """
    Return (chunk, relevance score) pairs to use as
    context, ordered by file, page and position.
    """

    all_chunks = _all_chunks(store)

    total_chars = sum(
        len(chunk.page_content)
        for chunk in all_chunks
    )

    # Small uploads: use every chunk so whole-document
    # analysis and multi-document questions work.
    # No similarity search is needed here, which saves
    # an embedding request per question.
    if total_chars <= FULL_CONTEXT_CHARS:

        results = [
            (chunk, 0.0)
            for chunk in all_chunks
        ]

    # Large uploads: top chunks from EACH document, so
    # every uploaded file is represented.
    else:

        results = []

        for source in sources:

            results.extend(
                store.similarity_search_with_relevance_scores(
                    question,
                    k=CHUNKS_PER_DOCUMENT,
                    filter={"source": source}
                )
            )

    return sorted(
        results,
        key=lambda item: (
            item[0].metadata.get("source", ""),
            item[0].metadata.get("page", 0),
            item[0].metadata.get("chunk", 0)
        )
    )


def _format_sources(
    documents: list[Document],
    names: list[str]
) -> list[str]:
    """
    "file.pdf (pages 1, 2)" for each cited file.
    """

    formatted = []

    for name in names:

        pages = sorted(
            {
                document.metadata["page"]
                for document in documents
                if document.metadata.get("source") == name
                and "page" in document.metadata
            }
        )

        if pages:

            label = "page" if len(pages) == 1 else "pages"

            formatted.append(
                f"{name} ({label} {', '.join(map(str, pages))})"
            )

        else:

            formatted.append(name)

    return formatted


# =========================================================
# ASK UPLOADED DOCUMENTS
# =========================================================

def ask_documents(
    session_id: str | None,
    question: str,
    contextual_question: str | None = None
):
    """
    Answer a question from the session's uploaded
    documents. Returns None when the question is
    not about the uploaded documents, so the
    existing travel routing handles it.
    """

    if not has_documents(session_id):
        return None

    store = _get_store(session_id)

    sources = sorted(
        {
            metadata.get("source", "unknown")
            for metadata in store.get(include=["metadatas"])["metadatas"]
        }
    )

    # ---------------------------------------------
    # Is this question about the uploads?
    # ---------------------------------------------

    # Definitely about the uploads: first question after
    # an upload, or the question mentions the documents.
    definitely_documents = (
        session_id in _pending_upload
        or _refers_to_documents(question, sources)
    )

    _pending_upload.discard(session_id)

    # Nearby-places questions ("cafes near X") go straight
    # to normal routing, saving a Gemini request.
    if (
        not definitely_documents
        and "nearby" in detect_intents(question)
    ):
        return None

    results = _retrieve(store, question, sources)

    documents = [document for document, _ in results]

    # =====================================================
    # GEMINI
    # =====================================================
    # Document questions always go to Gemini, even when
    # MOCK_LLM is enabled for the travel RAG, so the
    # retrieved content is analysed, never returned raw.

    context = "\n\n".join(
        (
            f"[Source: {document.metadata.get('source', 'unknown')}"
            + (
                f", page {document.metadata['page']}"
                if "page" in document.metadata
                else ""
            )
            + "]\n"
            + document.page_content
        )
        for document in documents
    )

    prompt = DOCUMENT_PROMPT.format(
        relevance_instruction=(
            "" if definitely_documents
            else RELEVANCE_INSTRUCTION
        ),
        not_found=NOT_FOUND_MESSAGE,
        context=context,
        question=contextual_question or question
    )

    answer = extract_text(
        llm.invoke(prompt)
    ).strip()

    if (
        not definitely_documents
        and answer.strip('"').startswith(NOT_RELEVANT_MARKER)
    ):
        return None

    # Don't cite sources for a "not found" answer. Only
    # match a reply that is just the message, so a full
    # analysis noting one missing detail is kept.
    if (
        answer.strip('"').strip().lower()
        == NOT_FOUND_MESSAGE.lower()
    ):

        return {
            "answer": NOT_FOUND_MESSAGE,
            "sources": []
        }

    # Prefer the files Gemini actually cited.
    cited = [
        source
        for source in sources
        if source in answer
    ]

    return {
        "answer": answer,
        "sources": _format_sources(
            documents,
            cited or sources
        )
    }
