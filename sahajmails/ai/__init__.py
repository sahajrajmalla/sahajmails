"""Optional AI personalization.

The model fills labelled gaps in your template and never sees, or can alter,
anything else. Review is the default: text is generated, you read and edit it,
and only then is it sent — verbatim.

Nothing here is required. A template with ``{% ai %}`` blocks renders perfectly
with the AI layer switched off, using each block's fallback text.
"""

from __future__ import annotations

from .backends import PROVIDERS
from .base import (
    AIBackend,
    Completion,
    FakeBackend,
    available_backends,
    create_backend,
    register_backend,
)
from .engine import SlotEngine, SlotFill, SlotResult, clean_output, csv_to_slots, slots_to_csv

__all__ = [
    "PROVIDERS",
    "AIBackend",
    "Completion",
    "FakeBackend",
    "SlotEngine",
    "SlotFill",
    "SlotResult",
    "available_backends",
    "clean_output",
    "create_backend",
    "csv_to_slots",
    "register_backend",
    "slots_to_csv",
]
