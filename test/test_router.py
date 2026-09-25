from services.router import route_question


questions = [
    "What are popular places to visit in Ooty?",
    "Find restaurants near RS Puram",
    "Good cafes in Gandhipuram",
    "What food is Coimbatore famous for?",
    "What hotels are available in Munnar?",
    "I'm visiting Coimbatore for 2 days. What places should I visit and where can I eat?"
]


for question in questions:

    route = route_question(question)

    print(
        f"\nQuestion: {question}"
    )

    print(
        f"Route: {route}"
    )