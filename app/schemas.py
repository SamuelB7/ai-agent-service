from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class HealthResponse(BaseModel):
    status: str
    service: str


class CamelModel(BaseModel):
    model_config = ConfigDict(populate_by_name=True)


class ChatRequest(CamelModel):
    conversation_id: str = Field(alias="conversationId", min_length=1)
    user_id: str = Field(alias="userId", min_length=1)
    message: str = Field(min_length=1)


class ToolCall(CamelModel):
    name: str
    status: str
    input: dict[str, Any] = Field(default_factory=dict)
    output: Any | None = None
    error: str | None = None


class ChatResponse(BaseModel):
    content: str
    evidence: list[dict[str, Any]] = Field(default_factory=list)
    logs: list[dict[str, Any]] = Field(default_factory=list)
    tools: list[ToolCall] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)
