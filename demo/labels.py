"""Label normalization helpers."""


def normalize_label(text: str) -> str:
    """Return a trimmed, whitespace-collapsed, casefolded label."""
    if not isinstance(text, str):
        raise TypeError("text must be a string")
    normalized = " ".join(text.split()).casefold()
    if not normalized:
        raise ValueError("text must contain non-whitespace characters")
    return normalized
