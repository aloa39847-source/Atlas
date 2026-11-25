from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any

from app.core.enums import EngineStatus


class Engine(ABC):
    key: str
    name: str

    @abstractmethod
    def status(self) -> EngineStatus:
        raise NotImplementedError

    def describe(self) -> dict[str, Any]:
        return {
            "key": self.key,
            "name": self.name,
            "status": self.status().value,
        }
