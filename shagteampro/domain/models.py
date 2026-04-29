from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime


@dataclass
class Card:
    id: int
    name: str
    created_at: datetime


@dataclass
class KeyPhrase:
    id: int
    card_id: int
    phrase: str
    created_at: datetime
    search_enabled: bool
    maps_enabled: bool
