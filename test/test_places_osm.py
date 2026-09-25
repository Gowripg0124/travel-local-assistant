from services.places_service import search_places


history = [
    {
        "role": "user",
        "content": "I'm staying in Peelamedu, Coimbatore."
    }
]


result = search_places(
    "Find restaurants nearby.",
    history=history,
    max_results=5
)


print("\nRESULT:\n")
print(result)