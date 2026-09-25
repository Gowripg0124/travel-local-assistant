from rag.rag_service import ask_rag


question = "What are popular places to visit in Ooty?"


result = ask_rag(question)


print("\n==============================")
print("QUESTION")
print("==============================")

print(question)


print("\n==============================")
print("ANSWER")
print("==============================")

print(result["answer"])


print("\n==============================")
print("SOURCES")
print("==============================")

for source in result["sources"]:

    print(f"📄 {source}")