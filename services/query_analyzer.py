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

        r"^(?:what|how)\s+about\s+(.+?)(?:[.!?]|$)",

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

        location = location.strip(" ,.")

        if not location:
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