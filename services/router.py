from services.query_analyzer import analyze_query


def route_question(
    question: str,
    history=None
) -> str:

    analysis = analyze_query(
        question,
        history
    )

    intents = analysis["intents"]

    if "conversation" in intents:
        return "conversation"

    places_intents = [
        "restaurant",
        "cafe",
        "hotel"
    ]

    rag_intents = [
        "attraction",
        "food",
        "general"
    ]

    has_places_intent = any(
        intent in places_intents
        for intent in intents
    )

    has_rag_intent = any(
        intent in rag_intents
        for intent in intents
    )

    if has_places_intent and has_rag_intent:
        return "both"

    if has_places_intent:
        return "places"

    return "rag"