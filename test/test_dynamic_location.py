from services.query_analyzer import analyze_query


questions = [
    "I'm staying in Saravanampatti.",
    "I'm staying in Peelamedu.",
    "I'm visiting Bangalore.",
    "Find restaurants near Brookefields.",
]


for question in questions:

    result = analyze_query(
        question
    )

    print("\nQuestion:", question)
    print("Intents:", result["intents"])
    print("Location:", result["location"])