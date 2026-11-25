"""Base Agent - Specialized agent framework"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass
class AgentVote:
    agent_name: str
    role: str
    bias: str  # bullish/bearish/neutral
    confidence: float
    evidence: list[str]
    reasoning: str
    risk_notes: list[str]
    veto: bool = False


class BaseAgent:
    def __init__(self, name: str, role: str):
        self.name = name
        self.role = role

    def analyze(self, context: dict[str, Any]) -> AgentVote:
        raise NotImplementedError
