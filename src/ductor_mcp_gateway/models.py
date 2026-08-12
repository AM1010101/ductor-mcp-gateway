"""Typed data-in/data-out models for Ductor's internal HTTP API."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

AgentName = str
TaskPriority = Literal["interactive", "background", "batch"]
TransportName = Literal["mcp", "telegram", "tg", "matrix", "mx"]

_AGENT_PATTERN = r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,63}$"
_TASK_ID_PATTERN = r"^[A-Za-z0-9][A-Za-z0-9_-]{0,127}$"
_MAX_TEXT = 200_000


class _Request(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)


class _Response(BaseModel):
    model_config = ConfigDict(extra="allow")


class AgentMessageRequest(_Request):
    """Payload for ``POST /interagent/send``."""

    from_agent: AgentName = Field(
        validation_alias="from", serialization_alias="from", pattern=_AGENT_PATTERN
    )
    to_agent: AgentName = Field(
        validation_alias="to", serialization_alias="to", pattern=_AGENT_PATTERN
    )
    message: str = Field(min_length=1, max_length=_MAX_TEXT)
    new_session: bool = False
    chat_id: int = Field(default=0, ge=-(2**63), le=2**63 - 1)
    topic_id: int | None = Field(default=None, ge=1, le=2**63 - 1)

    @field_validator("message")
    @classmethod
    def message_is_not_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("message must not be blank")
        return value


class AgentMessageAsyncRequest(AgentMessageRequest):
    """Payload for ``POST /interagent/send_async``."""

    summary: str = Field(default="", max_length=1_000)
    transport: TransportName = "mcp"
    reply_to: AgentName | None = Field(default=None, pattern=_AGENT_PATTERN)
    silent: bool = True


class TaskCreateRequest(_Request):
    """Payload for ``POST /tasks/create``."""

    from_agent: AgentName = Field(
        validation_alias="from", serialization_alias="from", pattern=_AGENT_PATTERN
    )
    prompt: str = Field(min_length=1, max_length=_MAX_TEXT)
    name: str = Field(default="", max_length=200)
    provider: str | None = Field(default=None, max_length=100)
    model: str | None = Field(default=None, max_length=200)
    thinking: str | None = Field(default=None, max_length=100)
    priority: TaskPriority = "background"
    chat_id: int = Field(default=0, ge=-(2**63), le=2**63 - 1)
    topic_id: int | None = Field(default=None, ge=1, le=2**63 - 1)

    @field_validator("prompt")
    @classmethod
    def prompt_is_not_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("prompt must not be blank")
        return value


class TaskListRequest(_Request):
    """Query for ``GET /tasks/list``."""

    from_agent: AgentName = Field(
        validation_alias="from", serialization_alias="from", pattern=_AGENT_PATTERN
    )


class TaskResumeRequest(_Request):
    """Payload for ``POST /tasks/resume``."""

    from_agent: AgentName = Field(
        validation_alias="from", serialization_alias="from", pattern=_AGENT_PATTERN
    )
    task_id: str = Field(pattern=_TASK_ID_PATTERN)
    prompt: str = Field(min_length=1, max_length=_MAX_TEXT)

    @field_validator("prompt")
    @classmethod
    def prompt_is_not_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("prompt must not be blank")
        return value


class TaskCancelRequest(_Request):
    """Payload for ``POST /tasks/cancel``."""

    from_agent: AgentName = Field(
        validation_alias="from", serialization_alias="from", pattern=_AGENT_PATTERN
    )
    task_id: str = Field(pattern=_TASK_ID_PATTERN)


class AgentsResponse(_Response):
    agents: list[str]


class AgentMessageResponse(_Response):
    sender: str = ""
    text: str = ""
    success: bool
    error: str = ""


class AsyncMessageResponse(_Response):
    success: bool
    task_id: str | None = None
    error: str = ""


class TaskMutationResponse(_Response):
    success: bool
    task_id: str | None = None
    error: str = ""


class TaskCancelResponse(_Response):
    success: bool
    error: str = ""


class TaskRecord(_Response):
    """Known task fields, while preserving additional fields from newer Ductor releases."""

    task_id: str
    parent_agent: str
    chat_id: int = 0
    name: str = ""
    prompt_preview: str = ""
    original_prompt: str = ""
    provider: str = ""
    model: str = ""
    status: str
    session_id: str = ""
    created_at: float = 0.0
    completed_at: float = 0.0
    elapsed_seconds: float = 0.0
    error: str = ""
    result_preview: str = ""
    question_count: int = 0
    num_turns: int = 0
    last_question: str = ""
    thinking: str = ""
    reasoning_effort: str = ""
    tasks_dir: str = ""
    thread_id: int | None = None
    priority: str = "background"


class TasksResponse(_Response):
    tasks: list[TaskRecord]


class HealthResponse(_Response):
    agents: dict[str, dict[str, Any]]
