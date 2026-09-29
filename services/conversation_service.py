import hashlib
import re

from services import database


# =========================================================
# CONFIGURATION
# =========================================================

MAX_TITLE_LENGTH = 50

DEFAULT_TITLE = "New chat"

# Leading phrases that don't belong in a title.
# Applied repeatedly, in order.
LEADING_FILLER_PATTERNS = [
    r"(hi|hello|hey)\b[,!.\s]*",
    r"(can|could|would|will)\s+you\s+",
    r"please\s+",
    r"(i\s+want\s+to|i'?d\s+like\s+to|i\s+would\s+like\s+to|help\s+me)\s+",
    r"(find\s+me|find|show\s+me|show|search\s+for|search|look\s+for|"
    r"get\s+me|get|give\s+me|list|tell\s+me\s+about|tell\s+me)\s+",
]

# "Analyse X" -> "X analysis"
VERB_TO_NOUN = {
    "analyse": "analysis",
    "analyze": "analysis",
    "summarise": "summary",
    "summarize": "summary",
    "compare": "comparison",
    "review": "review",
    "explain": "explanation",
    "evaluate": "evaluation",
}

DETERMINERS = r"(this|that|these|those|the|my|our|a|an)\s+"


class ConversationError(Exception):
    """
    Raised for missing or foreign conversations.
    The message is safe to show to the user.
    """

    def __init__(self, message: str, status_code: int):

        super().__init__(message)

        self.status_code = status_code


# =========================================================
# TITLES (deterministic, no LLM call)
# =========================================================

def make_title(question: str) -> str:
    """
    Build a short title from the first question.

    "Find restaurants near RS Puram"
        -> "Restaurants near RS Puram"
    "Analyse this CCA score report"
        -> "CCA score report analysis"
    """

    title = " ".join((question or "").split())

    # Use only the first sentence.
    title = re.split(r"(?<=[.!?])\s+", title, maxsplit=1)[0]

    # Remove leading filler phrases.
    changed = True

    while changed:

        changed = False

        for pattern in LEADING_FILLER_PATTERNS:

            stripped = re.sub(
                rf"^{pattern}",
                "",
                title,
                flags=re.IGNORECASE
            )

            if stripped != title:
                title = stripped
                changed = True

    # "Analyse this X and ..." -> "X analysis"
    match = re.match(r"^(\w+)\s+(.+)$", title)

    if match and match.group(1).lower() in VERB_TO_NOUN:

        subject = re.split(
            r"\s+(?:and|then|so|to|for)\s+",
            match.group(2),
            maxsplit=1
        )[0]

        subject = re.sub(
            rf"^{DETERMINERS}",
            "",
            subject,
            flags=re.IGNORECASE
        ).strip(" .,!?;:")

        title = f"{subject} {VERB_TO_NOUN[match.group(1).lower()]}"

    title = title.strip(" .,!?;:")

    if len(title) > MAX_TITLE_LENGTH:

        title = (
            title[:MAX_TITLE_LENGTH].rsplit(" ", 1)[0]
            + "…"
        )

    if not title:
        return DEFAULT_TITLE

    return title[0].upper() + title[1:]


# =========================================================
# OWNERSHIP
# =========================================================

def get_owned_conversation(
    conversation_id: int,
    user_id: int
) -> dict:
    """
    Return the conversation if it belongs to the user,
    otherwise raise ConversationError.
    """

    conversation = database.get_conversation(conversation_id)

    if conversation is None:
        raise ConversationError(
            "Conversation not found.",
            status_code=404
        )

    if conversation["user_id"] != user_id:
        raise ConversationError(
            "You don't have access to this conversation.",
            status_code=403
        )

    return conversation


def document_store_key(
    user_id: int,
    conversation_id: int
) -> str:
    """
    Key for a conversation's uploaded documents in
    document_service. Includes the user id so two
    users' documents can never share a store.
    """

    return f"u{user_id}_c{conversation_id}"


def guest_store_key(guest_token: str) -> str:
    """
    Key for a guest's uploaded documents. Derived from
    a hash of the guest's random token, so guests can't
    reach each other's (or any user's) documents.
    """

    digest = hashlib.sha256(
        guest_token.encode("utf-8")
    ).hexdigest()

    return f"guest_{digest[:40]}"
