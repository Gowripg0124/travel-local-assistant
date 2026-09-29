import re
from rapidfuzz import process, fuzz


# =========================================================
# INTENT DETECTION
# =========================================================

INTENT_KEYWORDS = {
    "restaurant": [
        "restaurant",
        "restaurants",
    ],
    "cafe": [
        "cafe",
        "cafes",
        "coffee",
    ],
    "hotel": [
        "hotel",
        "hotels",
        "resort",
        "resorts",
    ],
    "food": [
        "food",
        "dish",
        "dishes",
        "cuisine",
    ],
    "attraction": [
        "visit",
        "visiting",
        "attraction",
        "attractions",
        "tourist",
    ],
}


# Words that follow "near"/"around" but aren't places.
NOT_A_LOCATION = {
    "me", "here", "my location", "my place",
    "my hotel", "where i am", "us"
}

# Place-type intents the live Places service can search.
PLACE_TYPE_INTENTS = {"restaurant", "cafe", "hotel"}

# Words showing the user wants a live search, not
# information ("difference between a hotel and a resort").
PLACE_SEARCH_WORDS = {
    "find", "show", "search", "list", "recommend",
    "suggest", "best", "top", "good", "any", "some",
    "nearby", "near", "around", "close", "looking"
}

# Phrases asking for knowledge about a place or topic.
KNOWLEDGE_PHRASES = [
    "famous for", "known for", "tell me about",
    "tell me more about", "information about",
    "history of", "things to do", "what to do",
    "what to see", "worth visiting", "should i visit",
    "places to visit", "attractions", "mentioned",
    "according to", "travel information", "travel guide",
    "travel documents"
]

# Phrases that explicitly ask what the travel documents
# say; these must not fall back to a general answer.
DOCUMENT_REFERENCE_PHRASES = [
    "mentioned", "according to", "travel information",
    "travel guide", "travel documents", "your documents",
    "the documents", "knowledge base"
]


def detect_intents(question: str) -> list[str]:

    question_lower = question.lower()

    intents = set()

    # -----------------------------------------
    # Exact phrase matching
    # -----------------------------------------

    exact_phrases = {
        "restaurant": [
            "where can i eat",
            "food near",
        ],
        "attraction": [
            "what should i see",
            "places to visit",
            "places should i visit",
        ],
    }

    for intent, phrases in exact_phrases.items():

        for phrase in phrases:

            if phrase in question_lower:
                intents.add(intent)

    # -----------------------------------------
    # Fuzzy word matching
    # -----------------------------------------

    words = question_lower.split()

    all_keywords = []

    for keywords in INTENT_KEYWORDS.values():
        all_keywords.extend(keywords)

    for word in words:

        # Remove punctuation
        word = word.strip(".,!?;:")

        # Ignore very short words
        if len(word) < 4:
            continue

        match = process.extractOne(
            word,
            all_keywords,
            scorer=fuzz.ratio
        )

        if not match:
            continue

        matched_word, score, _ = match

        # Require a strong fuzzy match
        if score < 85:
            continue

        # Find which intent owns the matched keyword
        for intent, keywords in INTENT_KEYWORDS.items():

            if matched_word in keywords:
                intents.add(intent)
                break

    # -----------------------------------------
    # Nearby intent
    # -----------------------------------------

    nearby_words = [
        "nearby",
        "near",
        "around",
        "close",
    ]

    for word in words:

        word = word.strip(".,!?;:")

        if word in nearby_words:
            intents.add("nearby")
            break

    return list(intents)


# =========================================================
# DYNAMIC LOCATION EXTRACTION
# =========================================================

def extract_location_from_text(text: str) -> str | None:

    if not text:
        return None

    text = text.strip()

    patterns = [

        # -----------------------------------------
        # "what about Gandhipuram?"
        # "how about Gandhipuram?"
        # -----------------------------------------

        # (skips "what about the food there?")
        r"^(?:what|how)\s+about\s+(?!(?:the|this|that|these|those|it|my|your|our|a|an)\b)(.+?)(?:[.!?]|$)",

        # -----------------------------------------
        # "hotels near Saravanampatti"
        # "restaurants near Gandhipuram, Coimbatore"
        # -----------------------------------------

        r"\bnear\s+(.+?)(?:[.!?]|$)",

        # -----------------------------------------
        # Context statements
        # -----------------------------------------

        r"\bstaying in\s+(.+?)(?:[.!?]|$)",
        r"\bstaying at\s+(.+?)(?:[.!?]|$)",
        r"\bstaying near\s+(.+?)(?:[.!?]|$)",

        r"\bliving in\s+(.+?)(?:[.!?]|$)",
        r"\bbased in\s+(.+?)(?:[.!?]|$)",

        # -----------------------------------------
        # Travel statements
        # -----------------------------------------

        r"\bvisiting\s+(.+?)\s+for\s+\d+\s+(?:day|days|night|nights)\b",

        r"\bvisiting\s+(.+?)(?:[.!?]|$)",

        r"\bgoing to\s+(.+?)(?:[.!?]|$)",

        r"\btraveling to\s+(.+?)(?:[.!?]|$)",
        r"\btravelling to\s+(.+?)(?:[.!?]|$)",

        # -----------------------------------------
        # "restaurants in Saravanampatti, Coimbatore"
        # (checked last; skips "in the/this/my ...")
        # -----------------------------------------

        r"\bin\s+(?!(?:the|this|that|these|those|my|your|our|a|an|it)\b)(.+?)(?:[.!?]|$)",

        # -----------------------------------------
        # Knowledge phrasing
        # "What is Ooty known for and ..."
        # "Tell me about Ooty and ..."
        # -----------------------------------------

        r"\b(?:what\s+is|what's)\s+(.+?)\s+(?:known|famous)\s+for\b",
        r"\btell\s+me\s+(?:more\s+)?about\s+(.+?)(?:\s+and\b|[.!?]|$)",
    ]

    for pattern in patterns:

        match = re.search(
            pattern,
            text,
            re.IGNORECASE
        )

        if not match:
            continue

        location = match.group(1).strip()

        # -----------------------------------------
        # Remove trailing travel context
        # -----------------------------------------

        location = re.sub(
            r"\s+for\s+\d+\s+(day|days|night|nights).*$",
            "",
            location,
            flags=re.IGNORECASE
        )

        # -----------------------------------------
        # Remove common trailing words
        # -----------------------------------------

        location = re.sub(
            r"\s+(for|and|where|what|please)$",
            "",
            location,
            flags=re.IGNORECASE
        )

        # Remove a trailing clause after the place:
        # "wayanad to visit" -> "wayanad"
        location = re.sub(
            r"\s+(?:to|for|which|that|with|where|what)\s+.*$",
            "",
            location,
            flags=re.IGNORECASE
        )

        location = location.strip(" ,.")

        if not location:
            continue

        # "near me" / "around here" aren't places.
        if location.lower() in NOT_A_LOCATION:
            continue

        # -----------------------------------------
        # Don't treat intent words as locations
        #
        # Example:
        # "what about cafes?"
        # -----------------------------------------

        location_words = (
            location.lower()
            .split()
        )

        intent_words = set()

        for keywords in INTENT_KEYWORDS.values():
            intent_words.update(keywords)

        cleaned_words = [
            word.strip(".,!?;:")
            for word in location_words
        ]

        if (
            len(cleaned_words) <= 3
            and all(
                word in intent_words
                for word in cleaned_words
            )
        ):
            continue

        return location

    return None

# =========================================================
# LOCATION FROM CONVERSATION
# =========================================================

def detect_location_from_history(history) -> str | None:

    if not history:
        return None

    for message in reversed(history):

        if isinstance(message, dict):

            role = message.get(
                "role",
                ""
            )

            content = message.get(
                "content",
                ""
            )

        else:

            role = getattr(
                message,
                "role",
                ""
            )

            content = getattr(
                message,
                "content",
                ""
            )

        if role.lower() != "user":
            continue

        location = extract_location_from_text(
            content
        )

        if location:
            return location

    return None


def detect_previous_location(
    history,
    current_question
) -> str | None:

    if not history:
        return None

    current_question_lower = (
        current_question.strip().lower()
    )

    # Search only USER messages.
    for message in reversed(history):

        if isinstance(message, dict):

            role = message.get(
                "role",
                ""
            )

            content = message.get(
                "content",
                ""
            )

        else:

            role = getattr(
                message,
                "role",
                ""
            )

            content = getattr(
                message,
                "content",
                ""
            )

        # Ignore assistant messages
        if role.lower() != "user":
            continue

        # Ignore current question
        if (
            content.strip().lower()
            == current_question_lower
        ):
            continue

        location = extract_location_from_text(
            content
        )

        if location:
            return location

    return None


# =========================================================
# PREVIOUS PLACE INTENT
# =========================================================

def detect_previous_place_intent(
    history,
    current_question
) -> str | None:

    if not history:
        return None

    current_question_lower = (
        current_question.strip().lower()
    )

    for message in reversed(history):

        if isinstance(message, dict):

            role = message.get(
                "role",
                ""
            )

            content = message.get(
                "content",
                ""
            )

        else:

            role = getattr(
                message,
                "role",
                ""
            )

            content = getattr(
                message,
                "content",
                ""
            )

        # Only inspect user messages
        if role.lower() != "user":
            continue

        # Skip current question
        if (
            content.strip().lower()
            == current_question_lower
        ):
            continue

        intents = detect_intents(content)

        for intent in [
            "restaurant",
            "cafe",
            "hotel",
        ]:

            if intent in intents:
                return intent

    return None


# =========================================================
# PARENT LOCATION
# =========================================================

def detect_parent_location(
    history,
    current_question
) -> str | None:

    if not history:
        return None

    current_question_lower = (
        current_question.strip().lower()
    )

    # -----------------------------------------
    # First preference:
    # Find an explicitly stated city/location
    #
    # Examples:
    # "I'm staying in Coimbatore"
    # "I live in Coimbatore"
    # "I'm based in Coimbatore"
    # -----------------------------------------

    explicit_parent_patterns = [
        r"\bstaying in\s+(.+?)(?:[.!?]|$)",
        r"\bliving in\s+(.+?)(?:[.!?]|$)",
        r"\bbased in\s+(.+?)(?:[.!?]|$)",
        r"\bi'?m in\s+(.+?)(?:[.!?]|$)",
        r"\bi am in\s+(.+?)(?:[.!?]|$)",
    ]

    for message in reversed(history):

        if isinstance(message, dict):
            role = message.get("role", "")
            content = message.get("content", "")
        else:
            role = getattr(message, "role", "")
            content = getattr(message, "content", "")

        if role.lower() != "user":
            continue

        if (
            content.strip().lower()
            == current_question_lower
        ):
            continue

        for pattern in explicit_parent_patterns:

            match = re.search(
                pattern,
                content,
                re.IGNORECASE
            )

            if match:

                parent = match.group(1).strip()

                parent = re.sub(
                    r"\s+for\s+\d+\s+(day|days|night|nights).*$",
                    "",
                    parent,
                    flags=re.IGNORECASE
                )

                parent = parent.strip(" ,.")

                if parent:
                    return parent

    return None
# =========================================================
# LOCATION CHANGE QUERY
# =========================================================

def is_location_change_query(
    question: str
) -> bool:

    question_lower = (
        question.lower().strip()
    )

    return (
        question_lower.startswith(
            "what about "
        )
        or question_lower.startswith(
            "how about "
        )
    )


# =========================================================
# ASSISTANT ASKED FOR LOCATION
# =========================================================

def assistant_asked_for_location(history) -> bool:
    """
    True when the last assistant message asked the
    user for a location (see places_service).
    """

    if not history:
        return False

    for message in reversed(history):

        if isinstance(message, dict):
            role = message.get("role", "")
            content = message.get("content", "")
        else:
            role = getattr(message, "role", "")
            content = getattr(message, "content", "")

        if role.lower() != "assistant":
            continue

        return content.startswith(
            "I need a location"
        )

    return False


# =========================================================
# QUESTION TYPE HELPERS (used by the router)
# =========================================================

def is_place_search(
    question: str,
    intents: list[str],
    history=None
) -> bool:
    """
    True when the user wants a live search for a place
    type, not information about it. Never depends on
    which travel documents exist.
    """

    if not PLACE_TYPE_INTENTS & set(intents):
        return False

    question_lower = question.lower()

    words = set(re.findall(r"[a-z']+", question_lower))

    return bool(
        "nearby" in intents
        or words & PLACE_SEARCH_WORDS
        or "where can i" in question_lower
        or "where to" in question_lower
        or extract_location_from_text(question)
        or is_location_change_query(question)
        or assistant_asked_for_location(history)
    )


def is_knowledge_question(
    question: str,
    intents: list[str]
) -> bool:
    """
    True when the question asks for information
    (e.g. "known for", "tell me about", attractions).
    """

    question_lower = question.lower()

    return bool(
        {"attraction", "food"} & set(intents)
        or any(
            phrase in question_lower
            for phrase in KNOWLEDGE_PHRASES
        )
    )


def refers_to_travel_documents(question: str) -> bool:
    """
    True when the question explicitly asks what the
    travel documents say ("attractions mentioned in X").
    """

    question_lower = question.lower()

    return any(
        phrase in question_lower
        for phrase in DOCUMENT_REFERENCE_PHRASES
    )


# =========================================================
# QUERY ANALYZER
# =========================================================

def analyze_query(
    question: str,
    history=None
) -> dict:

    # -----------------------------------------
    # Current question intent
    # -----------------------------------------

    intents = detect_intents(
        question
    )

    # -----------------------------------------
    # Current location
    # -----------------------------------------

    location = extract_location_from_text(
        question
    )

    # -----------------------------------------
    # Previous location
    # -----------------------------------------

    previous_location = detect_previous_location(
        history,
        question
    )

    # -----------------------------------------
    # Parent location
    # -----------------------------------------

    parent_location = detect_parent_location(
        history,
        question
    )

    # -----------------------------------------
    # Previous place intent
    # -----------------------------------------

    previous_place_intent = (
        detect_previous_place_intent(
            history,
            question
        )
    )

    # -----------------------------------------
    # Contextual place query
    #
    # Example:
    #
    # hotels near Saravanampatti
    #        ↓
    # what about Gandhipuram?
    #
    # becomes:
    #
    # hotels + Gandhipuram
    # -----------------------------------------

    if (
        not intents
        and previous_place_intent
        and is_location_change_query(
            question
        )
    ):
        intents = [
            previous_place_intent
        ]

    # -----------------------------------------
    # Reply to "I need a location..."
    #
    # Example:
    #
    # nearby restaurants
    #        ↓
    # I need a location ... (assistant)
    #        ↓
    # Saravanampatti, Coimbatore
    #
    # becomes:
    #
    # restaurant + Saravanampatti, Coimbatore
    # -----------------------------------------

    if (
        not intents
        and previous_place_intent
        and assistant_asked_for_location(history)
    ):
        intents = [
            previous_place_intent
        ]

        if not location:
            location = question.strip(" .,!?")

    # -----------------------------------------
    # Use previous location only when
    # current question has no location
    # -----------------------------------------

    if not location and previous_location:
        location = previous_location

    # -----------------------------------------
    # Conversation message
    # -----------------------------------------

    question_lower = question.lower()

    context_phrases = [
        "staying",
        "living",
        "based",
        "i'm in",
        "i am in",
        "i'm at",
        "i am at",
        "i'll be",
        "i will be",
    ]

    is_context_message = (
        location is not None
        and any(
            phrase in question_lower
            for phrase in context_phrases
        )
        and not intents
    )

    if is_context_message:

        intents.append(
            "conversation"
        )

    # -----------------------------------------
    # General question
    # -----------------------------------------

    if not intents:

        intents.append(
            "general"
        )

    return {
        "intents": intents,
        "location": location,
        "previous_location": previous_location,
        "parent_location": parent_location,
        "previous_place_intent": previous_place_intent,
    }