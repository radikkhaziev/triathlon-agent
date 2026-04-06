"""Custom Dramatiq helpers — auto-serialize Pydantic models in actor kwargs."""

import dramatiq
from pydantic import BaseModel

_original_message_with_options = dramatiq.Actor.message_with_options


def _patched_message_with_options(self, *, args=(), kwargs=None, **options):
    """Wrap message_with_options to auto-dump Pydantic models in kwargs."""
    if kwargs:
        kwargs = {k: v.model_dump() if isinstance(v, BaseModel) else v for k, v in kwargs.items()}
    return _original_message_with_options(self, args=args, kwargs=kwargs, **options)


dramatiq.Actor.message_with_options = _patched_message_with_options
