from rag.retriever import get_retriever


retriever = get_retriever()


question = "What are popular places to visit in Ooty?"


documents = retriever.invoke(question)


print("\nQuestion:")
print(question)


print("\nRetrieved Documents:")
print("--------------------")


for i, document in enumerate(documents, start=1):

    print(f"\nDocument {i}")

    print("Source:")
    print(document.metadata.get("source"))

    print("Content:")
    print(document.page_content)