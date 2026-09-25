from services.places_service import build_places_query


tests = [
    "Find restaurants near Saravanampatti.",
    "Find cafes near Peelamedu.",
    "Find restaurants near Brookefields.",
]


for question in tests:

    query = build_places_query(question)

    print("\nQuestion:", question)
    print("Places query:", query)