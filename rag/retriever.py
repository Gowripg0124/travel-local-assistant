from langchain_chroma import Chroma
from langchain_google_genai import GoogleGenerativeAIEmbeddings
from langchain_core.retrievers import BaseRetriever
from langchain_core.documents import Document
from dotenv import load_dotenv

from typing import List

load_dotenv()

VECTORSTORE_DIR = "vectorstore"

RELEVANCE_THRESHOLD = 0.65


class SimilarityThresholdRetriever(BaseRetriever):

    vectorstore: Chroma
    k: int = 4
    threshold: float = RELEVANCE_THRESHOLD

    def _get_relevant_documents(
        self,
        query: str,
        *,
        run_manager=None
    ) -> List[Document]:

        results = (
            self.vectorstore
            .similarity_search_with_relevance_scores(
                query,
                k=self.k
            )
        )

        return [
            document
            for document, score in results
            if score >= self.threshold
        ]


def get_retriever():

    embeddings = GoogleGenerativeAIEmbeddings(
        model="gemini-embedding-2"
    )

    vectorstore = Chroma(
        persist_directory=VECTORSTORE_DIR,
        embedding_function=embeddings
    )

    return SimilarityThresholdRetriever(
        vectorstore=vectorstore,
        k=4,
        threshold=RELEVANCE_THRESHOLD
    )