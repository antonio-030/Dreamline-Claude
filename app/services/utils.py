"""Gemeinsame Hilfsfunktionen für Services."""


def truncate_text(text: str, max_chars: int, suffix: str = "... [truncated]") -> str:
    """Kürzt Text auf max_chars Zeichen mit optionalem Suffix."""
    if not text or len(text) <= max_chars:
        return text
    return text[:max_chars - len(suffix)] + suffix
