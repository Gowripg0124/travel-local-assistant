from services.query_analyzer import analyze_query


questions = [
    "What are popular places to visit in Ooty?",
    "Find restaurants near RS Puram",
    "Find cafes near Gandhipuram",
    "What food is Coimbatore famous for?",
    "I'm visiting Munnar for 2 days",
]


for question in questions:

    print("\nQuestion:", question)

    result = analyze_query(question)

    print("Intents:", result["intents"])
    print("Location:", result["location"])