from fastapi import FastAPI

from app.schemas import HealthResponse


def create_app() -> FastAPI:
    app = FastAPI(
        title="AI Agent Service",
        version="0.1.0",
        description="Bootstrap service for future log investigation agents.",
    )

    @app.get("/health", response_model=HealthResponse)
    def health() -> HealthResponse:
        return HealthResponse(status="ok", service="ai-agent-service")

    return app


app = create_app()

