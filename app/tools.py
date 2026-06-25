from typing import Any

import httpx

from app.config import Settings
from app.intent import InvestigationIntent
from app.repositories import PostgresRepository


class ToolRunner:
    def __init__(self, settings: Settings, postgres: PostgresRepository) -> None:
        self._settings = settings
        self._postgres = postgres

    def run(self, tool_name: str, intent: InvestigationIntent, context: dict[str, Any]) -> dict[str, Any]:
        tool_input = _clean(intent.as_tool_input())

        try:
            if tool_name == "search_logs":
                output = self.search_logs(tool_input)
            elif tool_name == "aggregate_errors":
                output = self.aggregate_errors(tool_input)
            elif tool_name == "get_trace_timeline":
                output = self.get_trace_timeline(tool_input)
            elif tool_name == "search_docs":
                output = self.search_docs(intent.raw_message)
            elif tool_name == "generate_incident_report":
                output = self.generate_incident_report(intent, context)
            elif tool_name == "create_github_issue":
                output = self.create_github_issue(intent, context)
            else:
                raise ValueError(f"Unknown tool: {tool_name}")

            return {
                "name": tool_name,
                "status": "ok",
                "input": tool_input if tool_name != "search_docs" else {"query": intent.raw_message},
                "output": output,
            }
        except Exception as error:
            return {
                "name": tool_name,
                "status": "failed",
                "input": tool_input,
                "error": safe_error(error),
            }

    def search_logs(self, filters: dict[str, Any], limit: int = 10) -> list[dict[str, Any]]:
        body = {
            "size": limit,
            "sort": [{"timestamp": {"order": "desc", "unmapped_type": "date"}}],
            "query": build_log_query(filters),
        }

        response = httpx.post(
            f"{self._settings.opensearch_url}/{self._settings.opensearch_index}/_search",
            json=body,
            timeout=self._settings.request_timeout_seconds,
        )
        response.raise_for_status()
        payload = response.json()

        return [
            normalize_log_hit(hit)
            for hit in payload.get("hits", {}).get("hits", [])
        ]

    def aggregate_errors(self, filters: dict[str, Any]) -> list[dict[str, Any]]:
        return self._postgres.aggregate_errors(filters)

    def get_trace_timeline(self, filters: dict[str, Any], limit: int = 50) -> list[dict[str, Any]]:
        trace_id = filters.get("traceId")
        request_id = filters.get("requestId")

        if not trace_id and not request_id:
            return []

        body = {
            "size": limit,
            "sort": [{"timestamp": {"order": "asc", "unmapped_type": "date"}}],
            "query": {
                "bool": {
                    "filter": [
                        {"term": {"traceId": trace_id}} if trace_id else {"term": {"requestId": request_id}}
                    ]
                }
            },
        }

        response = httpx.post(
            f"{self._settings.opensearch_url}/{self._settings.opensearch_index}/_search",
            json=body,
            timeout=self._settings.request_timeout_seconds,
        )
        response.raise_for_status()
        payload = response.json()

        return [
            normalize_log_hit(hit)
            for hit in payload.get("hits", {}).get("hits", [])
        ]

    def search_docs(self, query: str) -> dict[str, Any]:
        response = httpx.post(
            f"{self._settings.docs_rag_url}/search",
            json={"query": query, "limit": 5},
            timeout=self._settings.request_timeout_seconds,
        )
        response.raise_for_status()
        return response.json()

    def generate_incident_report(self, intent: InvestigationIntent, context: dict[str, Any]) -> dict[str, Any]:
        logs = context.get("logs", [])
        error_groups = context.get("errorGroups", [])
        docs = context.get("docs", [])
        severity = infer_severity(logs, error_groups)
        title = build_title(intent, logs)

        return {
            "title": title,
            "severity": severity,
            "summary": summarize_findings(intent, logs, error_groups, docs),
            "impact": "Impacto provável precisa ser confirmado com métricas de negócio e volume de erros.",
            "evidence": {
                "logs": logs[:5],
                "errorGroups": error_groups[:5],
                "docs": docs[:3],
            },
            "hypothesis": root_cause_hypothesis(intent, logs, error_groups, docs),
            "nextSteps": next_steps(intent, logs, error_groups, docs),
        }

    def create_github_issue(self, intent: InvestigationIntent, context: dict[str, Any]) -> dict[str, Any]:
        report = context.get("report") or self.generate_incident_report(intent, context)
        body = format_issue_body(report)

        if not self._settings.github_configured:
            return {
                "mode": "draft",
                "title": report["title"],
                "body": body,
                "labels": ["incident", "debugops-agent"],
                "reason": "GitHub integration is not configured.",
            }

        response = httpx.post(
            f"https://api.github.com/repos/{self._settings.github_owner}/{self._settings.github_repo}/issues",
            headers={
                "accept": "application/vnd.github+json",
                "authorization": f"Bearer {self._settings.github_token}",
                "content-type": "application/json",
                "x-github-api-version": "2022-11-28",
            },
            json={
                "title": report["title"],
                "body": body,
                "labels": ["incident", "debugops-agent"],
            },
            timeout=self._settings.request_timeout_seconds,
        )
        response.raise_for_status()
        issue = response.json()

        return {
            "mode": "created",
            "url": issue.get("html_url"),
            "number": issue.get("number"),
        }


def build_log_query(filters: dict[str, Any]) -> dict[str, Any]:
    must: list[dict[str, Any]] = []
    query_filters: list[dict[str, Any]] = []

    for key in ["service", "environment", "level", "requestId", "traceId"]:
        if filters.get(key):
            query_filters.append({"term": {key: filters[key]}})

    if filters.get("from") or filters.get("to"):
        query_filters.append(
            {
                "range": {
                    "timestamp": {
                        "gte": filters.get("from"),
                        "lte": filters.get("to"),
                    }
                }
            }
        )

    if filters.get("message"):
        must.append({"match": {"message": filters["message"]}})

    return {"bool": {"filter": query_filters, "must": must}}


def normalize_log_hit(hit: dict[str, Any]) -> dict[str, Any]:
    source = hit.get("_source") or {}
    return {
        "id": hit.get("_id") or source.get("id"),
        "service": source.get("service", ""),
        "environment": source.get("environment", ""),
        "level": source.get("level", ""),
        "message": source.get("message", ""),
        "timestamp": source.get("timestamp", ""),
        "requestId": source.get("requestId") or source.get("request_id"),
        "traceId": source.get("traceId") or source.get("trace_id"),
        "spanId": source.get("spanId") or source.get("span_id"),
        "stackTrace": source.get("stackTrace") or source.get("stack_trace"),
        "errorSignature": source.get("errorSignature"),
    }


def summarize_findings(intent: InvestigationIntent, logs: list[dict], error_groups: list[dict], docs: list[dict]) -> str:
    parts = [f"Investigação para: {intent.raw_message}"]
    if logs:
        parts.append(f"Foram encontrados {len(logs)} logs relevantes.")
    if error_groups:
        parts.append(f"Foram encontrados {len(error_groups)} agrupamentos de erro.")
    if docs:
        parts.append(f"Foram encontrados {len(docs)} trechos de documentação relacionados.")
    if len(parts) == 1:
        parts.append("Nenhuma evidência foi encontrada nas tools executadas.")
    return " ".join(parts)


def root_cause_hypothesis(
    intent: InvestigationIntent,
    logs: list[dict],
    error_groups: list[dict],
    docs: list[dict],
) -> str:
    if error_groups:
        top = error_groups[0]
        return f"A causa mais provável está associada ao grupo {top.get('signature')} em {top.get('service')}."
    if logs:
        first = logs[0]
        return f"A causa mais provável está relacionada ao log {first.get('id')} no serviço {first.get('service')}."
    if docs:
        return "A documentação relacionada deve ser usada para orientar a próxima etapa de investigação."
    return "Ainda não há evidência suficiente para apontar uma causa raiz."


def next_steps(intent: InvestigationIntent, logs: list[dict], error_groups: list[dict], docs: list[dict]) -> list[str]:
    steps = [
        "Confirmar janela de impacto e serviços afetados.",
        "Verificar métricas e traces relacionados ao mesmo requestId ou traceId.",
    ]
    if error_groups:
        steps.append("Priorizar o agrupamento de erro com maior ocorrência.")
    if docs:
        steps.append("Comparar a evidência encontrada com o runbook ou documentação retornada.")
    if not logs:
        steps.append("Ampliar a janela de busca ou informar serviço, ambiente, requestId ou traceId.")
    return steps


def infer_severity(logs: list[dict], error_groups: list[dict]) -> str:
    if error_groups and int(error_groups[0].get("occurrence_count") or 0) >= 50:
        return "critical"
    if any(log.get("level") == "fatal" for log in logs):
        return "critical"
    if any(log.get("level") == "error" for log in logs) or error_groups:
        return "high"
    if any(log.get("level") == "warn" for log in logs):
        return "medium"
    return "low"


def build_title(intent: InvestigationIntent, logs: list[dict]) -> str:
    service = intent.service or (logs[0].get("service") if logs else None) or "serviço monitorado"
    return f"Investigar incidente em {service}"


def format_issue_body(report: dict[str, Any]) -> str:
    next_steps_text = "\n".join(f"- {step}" for step in report.get("nextSteps", []))
    return (
        f"## Summary\n{report.get('summary')}\n\n"
        f"## Severity\n{report.get('severity')}\n\n"
        f"## Hypothesis\n{report.get('hypothesis')}\n\n"
        f"## Next steps\n{next_steps_text}\n"
    )


def _clean(value: dict[str, Any]) -> dict[str, Any]:
    return {key: item for key, item in value.items() if item is not None}


def safe_error(error: Exception) -> str:
    return str(error)[:500]
