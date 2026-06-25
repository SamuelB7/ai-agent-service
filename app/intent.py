from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
import re


@dataclass
class InvestigationIntent:
    raw_message: str
    action: str
    service: str | None = None
    environment: str | None = None
    level: str | None = None
    message_query: str | None = None
    request_id: str | None = None
    trace_id: str | None = None
    from_timestamp: str | None = None
    to_timestamp: str | None = None
    wants_docs: bool = False
    wants_report: bool = False
    wants_github_issue: bool = False
    wants_aggregation: bool = False
    wants_timeline: bool = False
    selected_tools: list[str] = field(default_factory=list)

    def as_tool_input(self) -> dict:
        return {
            "service": self.service,
            "environment": self.environment,
            "level": self.level,
            "message": self.message_query,
            "requestId": self.request_id,
            "traceId": self.trace_id,
            "from": self.from_timestamp,
            "to": self.to_timestamp,
        }


def interpret_message(message: str, history: list[dict] | None = None) -> InvestigationIntent:
    text = message.strip()
    lowered = text.lower()
    now = datetime.now(timezone.utc)
    from_timestamp, to_timestamp = _extract_time_range(lowered, now)

    intent = InvestigationIntent(
        raw_message=text,
        action=_classify_action(lowered),
        service=_extract_named_value(lowered, ["serviço", "service", "app", "aplicação", "aplicacao"]),
        environment=_extract_environment(lowered),
        level=_extract_level(lowered),
        message_query=_extract_message_query(text),
        request_id=_extract_identifier(text, ["request id", "request_id", "requestid", "req id"]),
        trace_id=_extract_identifier(text, ["trace id", "trace_id", "traceid"]),
        from_timestamp=from_timestamp,
        to_timestamp=to_timestamp,
        wants_docs=_contains_any(lowered, ["doc", "documentação", "documentacao", "runbook", "guia"]),
        wants_report=_contains_any(lowered, ["relatório", "relatorio", "report", "incidente", "incident"]),
        wants_github_issue=_contains_any(lowered, ["github", "issue", "abrir issue", "criar issue"]),
        wants_aggregation=_contains_any(lowered, ["agrupar", "agrupamento", "aggregate", "frequência", "frequencia", "parecidos"]),
        wants_timeline=_contains_any(lowered, ["timeline", "linha do tempo", "trace", "request id", "trace id"]),
    )
    intent.selected_tools = select_tools(intent, history or [])

    return intent


def select_tools(intent: InvestigationIntent, history: list[dict]) -> list[str]:
    tools: list[str] = ["search_logs"]

    if intent.wants_aggregation or intent.level in {"error", "fatal"}:
        tools.append("aggregate_errors")

    if intent.wants_timeline or intent.trace_id or intent.request_id:
        tools.append("get_trace_timeline")

    if intent.wants_docs:
        tools.append("search_docs")

    if intent.wants_report or intent.wants_github_issue:
        tools.append("generate_incident_report")

    if intent.wants_github_issue:
        tools.append("create_github_issue")

    return _dedupe(tools)


def _classify_action(text: str) -> str:
    if _contains_any(text, ["github", "issue", "abrir issue", "criar issue"]):
        return "create_issue"
    if _contains_any(text, ["relatório", "relatorio", "report", "incidente"]):
        return "generate_report"
    if _contains_any(text, ["timeline", "linha do tempo"]):
        return "trace_timeline"
    if _contains_any(text, ["agrupar", "agrupamento", "aggregate"]):
        return "aggregate_errors"
    return "investigate"


def _extract_time_range(text: str, now: datetime) -> tuple[str | None, str | None]:
    if "hoje" in text:
        start = now.replace(hour=0, minute=0, second=0, microsecond=0)
        return start.isoformat(), now.isoformat()

    match = re.search(r"(últim[ao]s?|ultim[ao]s?|last)\s+(\d+)\s+(minutos?|minutes?|horas?|hours?)", text)
    if match:
        amount = int(match.group(2))
        unit = match.group(3)
        delta = timedelta(minutes=amount) if unit.startswith(("min", "minute")) else timedelta(hours=amount)
        return (now - delta).isoformat(), now.isoformat()

    if "última hora" in text or "ultima hora" in text or "last hour" in text:
        return (now - timedelta(hours=1)).isoformat(), now.isoformat()

    return None, None


def _extract_named_value(text: str, labels: list[str]) -> str | None:
    for label in labels:
        match = re.search(rf"{label}\s+([a-z0-9_.:/-]+)", text)
        if match:
            return match.group(1).strip(".,;")
    return None


def _extract_environment(text: str) -> str | None:
    explicit = _extract_named_value(text, ["ambiente", "environment", "env"])
    if explicit:
        return explicit

    for env in ["production", "prod", "staging", "stage", "development", "dev", "local"]:
        if re.search(rf"\b{env}\b", text):
            return "production" if env == "prod" else env
    return None


def _extract_level(text: str) -> str | None:
    levels = {
        "fatal": "fatal",
        "error": "error",
        "erro": "error",
        "erros": "error",
        "warn": "warn",
        "warning": "warn",
        "warnings": "warn",
        "debug": "debug",
        "info": "info",
        "trace": "trace",
    }

    for raw, normalized in levels.items():
        if re.search(rf"\b{raw}\b", text):
            return normalized
    return None


def _extract_message_query(text: str) -> str | None:
    quoted = re.search(r"['\"]([^'\"]{3,})['\"]", text)
    if quoted:
        return quoted.group(1)

    for marker in ["mensagem", "message", "contendo", "contém", "contem"]:
        match = re.search(rf"{marker}\s+(.+)$", text, flags=re.IGNORECASE)
        if match:
            return match.group(1).strip()
    return None


def _extract_identifier(text: str, labels: list[str]) -> str | None:
    for label in labels:
        pattern = label.replace(" ", r"\s*")
        match = re.search(rf"{pattern}\s*[:=#]?\s*([a-zA-Z0-9_.:-]+)", text, flags=re.IGNORECASE)
        if match:
            return match.group(1).strip(".,;")
    return None


def _contains_any(text: str, values: list[str]) -> bool:
    return any(value in text for value in values)


def _dedupe(values: list[str]) -> list[str]:
    result: list[str] = []
    for value in values:
        if value not in result:
            result.append(value)
    return result
