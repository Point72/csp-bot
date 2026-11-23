"""Utility functions for csp-bot using chatom.

This module provides utility functions that leverage chatom's
cross-platform capabilities for mentions, formatting, etc.
"""

from typing import Literal
from urllib.parse import urlparse

from chatom import User, mention_user_for_backend
from chatom.format import Format, FormattedMessage, get_format_for_backend

__all__ = (
    "Backend",
    "format_message",
    "format_with_message_ml",
    "get_backend_format",
    "is_valid_url",
    "mention_user",
    "mention_users",
    "recursive_format_for_message_ml",
    "sanitize_message",
)

Backend = Literal["discord", "irc", "matrix", "slack", "symphony", "telegram", "zulip"]


def is_valid_url(url: str) -> bool:
    """Check if a string is a valid URL.

    Args:
        url: The string to check.

    Returns:
        True if the string is a valid URL.
    """
    try:
        result = urlparse(url)
        return all([result.scheme, result.netloc])
    except ValueError:
        return False


def mention_user(
    user: User,
    backend: Backend,
) -> str:
    """Generate a platform-specific mention for a user.

    Uses chatom's unified mention system to generate the correct
    mention format for each backend.

    Args:
        user: The chatom User to mention.
        backend: The target backend platform.

    Returns:
        The formatted mention string.

    Example:
        >>> user = User(id="U123", name="John")
        >>> mention_user(user, "slack")
        '<@U123>'
        >>> mention_user(user, "symphony")
        '<mention uid="U123"/>'
    """
    return mention_user_for_backend(user, backend)


def mention_users(
    users: list,
    backend: Backend,
    separator: str = " ",
) -> str:
    """Generate platform-specific mentions for multiple users.

    Args:
        users: List of chatom Users to mention.
        backend: The target backend platform.
        separator: String to separate mentions.

    Returns:
        The formatted mention strings joined by separator.
    """
    return separator.join(mention_user(u, backend) for u in users)


def format_message(
    content: str,
    backend: Backend,
    formatted: FormattedMessage | None = None,
) -> str:
    """Format a message for a specific backend.

    If a FormattedMessage is provided, renders it for the backend.
    Otherwise, returns the content as-is.

    Args:
        content: Plain text content.
        backend: The target backend platform.
        formatted: Optional FormattedMessage for rich content.

    Returns:
        The formatted message string.
    """
    if formatted:
        return formatted.render_for(backend)
    return content


def get_backend_format(backend: Backend) -> Format:
    """Get the native format for a backend.

    Args:
        backend: The backend platform.

    Returns:
        The Format enum value for the backend.
    """
    return get_format_for_backend(backend)


def format_with_message_ml(text: str, to_message_ml: bool = True) -> str:
    """Convert text to/from Symphony MessageML format.

    Handles escaping/unescaping of special characters that have meaning
    in MessageML syntax.

    Args:
        text: The text to convert.
        to_message_ml: If True, escape for MessageML. If False, unescape from MessageML.

    Returns:
        The converted text.
    """
    pairs = [
        ("&", "&#38;"),
        ("<", "&lt;"),
        ("${", "&#36;{"),
        ("#{", "&#35;{"),
    ]

    for original, msg_ml_version in pairs:
        if to_message_ml:
            text = text.replace(original, msg_ml_version)
        else:
            text = text.replace(msg_ml_version, original)

    return text


def sanitize_message(text: str) -> str:
    """Sanitize text for use in Symphony MessageML.

    Escapes special characters that could be interpreted as MessageML syntax.

    Args:
        text: The text to sanitize.

    Returns:
        The sanitized text safe for MessageML.
    """
    return format_with_message_ml(text, to_message_ml=True)


def recursive_format_for_message_ml(d):
    """Recursively format a data structure for MessageML.

    Applies MessageML escaping to all string values in a nested
    data structure (dict, list, set, tuple).

    Args:
        d: The data structure to format.

    Returns:
        The formatted data structure with all strings escaped.
    """
    if isinstance(d, (list, set, tuple)):
        return [recursive_format_for_message_ml(v) for v in d]
    elif isinstance(d, dict):
        return {sanitize_message(str(k)): recursive_format_for_message_ml(v) for k, v in d.items()}
    else:
        return sanitize_message(str(d))
