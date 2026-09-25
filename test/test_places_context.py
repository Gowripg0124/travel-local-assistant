from services.places_service import build_places_query


history = [
    {
        "role": "user",
        "content": "I'm staying in RS Puram."
    }
]


question = "Find restaurants nearby."


query = build_places_query(
    question,
    history
)


print(query)