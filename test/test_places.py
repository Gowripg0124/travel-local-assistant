from services.places_service import (
    build_places_query,
    search_places
)


questions = [
    "Find restaurants near RS Puram",
    "Find cafes near Gandhipuram",
    "Find restaurants in Coimbatore"
]


for question in questions:

    print("\n" + "=" * 50)

    print(
        "Question:",
        question
    )

    query = build_places_query(
        question
    )

    print(
        "Places query:",
        query
    )

    result = search_places(
        question
    )

    print(
        "Places:"
    )

    for place in result["places"]:

        print(
            f"- {place['name']} "
            f"({place['address']})"
        )