"""Application policy for exposed tools and Ductor agent identities."""

from __future__ import annotations

from .models import TaskRecord


class PolicyError(ValueError):
    """An operation is outside the configured gateway policy."""


class GatewayPolicy:
    def __init__(self, *, allowed_agents: list[str], allowed_tools: list[str]) -> None:
        self.allowed_agents = frozenset(allowed_agents)
        self.allowed_tools = frozenset(allowed_tools)

    def require_agent(self, agent: str) -> None:
        if agent not in self.allowed_agents:
            raise PolicyError("agent is not allowed by gateway policy")

    def require_tool(self, tool: str) -> None:
        if tool not in self.allowed_tools:
            raise PolicyError("tool is not allowed by gateway policy")

    def filter_agents(self, agents: list[str]) -> list[str]:
        return [agent for agent in agents if agent in self.allowed_agents]

    @staticmethod
    def filter_owned_tasks(tasks: list[TaskRecord], owner: str) -> list[TaskRecord]:
        """Defence in depth if an upstream version ignores its ``from`` query filter."""
        return [task for task in tasks if task.parent_agent == owner]
