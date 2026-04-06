"""Custom Dramatiq helpers — auto-serialize Pydantic models in actor kwargs and results."""

import json
from datetime import date, datetime

import dramatiq
from dramatiq.encoder import JSONEncoder
from pydantic import BaseModel

# --- 1. Patch message_with_options: auto-dump Pydantic in kwargs ---

_original_message_with_options = dramatiq.Actor.message_with_options


def _patched_message_with_options(self, *, args=(), kwargs=None, **options):
    """Wrap message_with_options to auto-dump Pydantic models in kwargs."""
    if kwargs:
        kwargs = {k: v.model_dump() if isinstance(v, BaseModel) else v for k, v in kwargs.items()}
    return _original_message_with_options(self, args=args, kwargs=kwargs, **options)


dramatiq.Actor.message_with_options = _patched_message_with_options


# --- 2. Custom encoder: auto-dump Pydantic in pipeline results ---


class _PydanticJSONEncoder(json.JSONEncoder):
    def default(self, o):
        if isinstance(o, BaseModel):
            return o.model_dump()
        if isinstance(o, (date, datetime)):
            return o.isoformat()
        return super().default(o)


class PydanticEncoder(JSONEncoder):
    """Dramatiq encoder that serializes Pydantic models in results."""

    def encode(self, data: dict) -> bytes:
        return json.dumps(data, separators=(",", ":"), cls=_PydanticJSONEncoder).encode("utf-8")


dramatiq.set_encoder(PydanticEncoder())
