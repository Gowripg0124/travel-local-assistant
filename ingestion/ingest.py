from pathlib import Path

from dotenv import load_dotenv
from langchain_community.document_loaders import TextLoader
from langchain_text_splitters import RecursiveCharacterTextSplitter
from langchain_chroma import Chroma
from langchain_google_genai import GoogleGenerativeAIEmbeddings


# Load environment variables
load_dotenv()


# Paths
DATA_DIR = Path("data")
VECTORSTORE_DIR = "vectorstore"


def load_documents():

    documents = []

    print("Loading documents...")

    for file_path in DATA_DIR.glob("*.txt"):

        print(f"Loading: {file_path.name}")

        loader = TextLoader(
            str(file_path),
            encoding="utf-8"
        )

        docs = loader.load()

        # Add source information
        for doc in docs:
            doc.metadata["source"] = file_path.name

        documents.extend(docs)

    return documents


def create_vectorstore():

    # --------------------------------
    # 1. Load documents
    # --------------------------------

    documents = load_documents()

    print(
        f"\nTotal documents loaded: {len(documents)}"
    )


    # --------------------------------
    # 2. Split documents into chunks
    # --------------------------------

    splitter = RecursiveCharacterTextSplitter(
        chunk_size=500,
        chunk_overlap=100
    )

    chunks = splitter.split_documents(
        documents
    )

    print(
        f"Total chunks created: {len(chunks)}"
    )


    # --------------------------------
    # 3. Create embeddings
    # --------------------------------

    print("\nCreating embeddings...")

    embeddings = GoogleGenerativeAIEmbeddings(
        model="gemini-embedding-2"
    )


    # --------------------------------
    # 4. Store embeddings in Chroma
    # --------------------------------

    print("Creating Chroma vector database...")

    vectorstore = Chroma.from_documents(
        documents=chunks,
        embedding=embeddings,
        persist_directory=VECTORSTORE_DIR
    )

    print(
        "\nVector database created successfully!"
    )


if __name__ == "__main__":

    create_vectorstore()