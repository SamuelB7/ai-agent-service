from fastapi import FastAPI

from app.config import load_settings
from app.graph import InvestigationAgent
from app.llm import ResponseSynthesizer
from app.repositories import PostgresRepository
from app.schemas import ChatRequest, ChatResponse, HealthResponse
from app.tools import ToolRunner


def create_app() -> FastAPI:
    settings = load_settings()
    postgres = PostgresRepository(settings)
    agent = InvestigationAgent(
        postgres=postgres,
        tools=ToolRunner(settings=settings, postgres=postgres),
        synthesizer=ResponseSynthesizer(settings),
    )

    app = FastAPI(
        title="AI Agent Service",
        version="0.1.0",
        description="AI investigation agent for logs, traces, documentation and incidents.",
    )

    @app.get("/health", response_model=HealthResponse)
    def health() -> HealthResponse:
        return HealthResponse(status="ok", service="ai-agent-service")

    @app.post("/chat", response_model=ChatResponse)
    def chat(request: ChatRequest) -> ChatResponse:
        return agent.run(request)

    return app


app = create_app()
