"""Internationalization — gettext with contextvars for per-request language."""

import contextvars
import gettext
from pathlib import Path

_LOCALE_DIR = Path(__file__).resolve().parent.parent / "locale"
_TRANSLATIONS: dict[str, gettext.GNUTranslations] = {}
_current_language: contextvars.ContextVar[str] = contextvars.ContextVar("language", default="ru")


def set_language(lang: str) -> None:
    """Set language for the current context (request/actor)."""
    _current_language.set(lang)


def get_language() -> str:
    return _current_language.get()


def get_translator(language: str = "ru") -> gettext.GNUTranslations:
    if language not in _TRANSLATIONS:
        try:
            _TRANSLATIONS[language] = gettext.translation("messages", localedir=str(_LOCALE_DIR), languages=[language])
        except FileNotFoundError:
            _TRANSLATIONS[language] = gettext.NullTranslations()
    return _TRANSLATIONS[language]


def _(text: str) -> str:
    """Translate a string using current context language. Babel-compatible."""
    return get_translator(_current_language.get()).gettext(text)
