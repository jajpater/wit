import os

_NL_MESSAGES = {
    # Dictionary to be populated with translated strings
}

def _(message: str) -> str:
    """Translate a message to the user's language (currently supports nl or fallback to en)."""
    lang = os.environ.get("WIT_LANG", os.environ.get("LANG", ""))
    if lang.startswith("nl"):
        return _NL_MESSAGES.get(message, message)
    return message
