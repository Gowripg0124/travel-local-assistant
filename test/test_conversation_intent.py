from services.query_analyzer import analyze_query


question = "I'm staying in RS Puram."


result = analyze_query(question)


print("Question:", question)
print("Intents:", result["intents"])
print("Location:", result["location"])