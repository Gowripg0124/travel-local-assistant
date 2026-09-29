from services.query_analyzer import (
    analyze_query,
    is_knowledge_question,
    is_place_search
)


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

    # A live search for a place type in any location.
    # Never depends on which travel documents exist.
    has_places_intent = is_place_search(
        question,
        intents,
        history
    )

    # Asks for information about a place or topic.
    has_rag_intent = is_knowledge_question(
        question,
        intents
    )

    if has_places_intent and has_rag_intent:
        return "both"

    if has_places_intent:
        return "places"

    # Knowledge questions. The API answers from the
    # travel documents when relevant ones exist, and
    # otherwise falls back to a general answer.
    return "rag"