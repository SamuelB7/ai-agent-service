from typing import Any

from app.config import Settings


class ResponseSynthesizer:
    def __init__(self, settings: Settings) -> None:
        self._settings = settings

    def synthesize(self, *, message: str, intent: dict[str, Any], tool_results: list[dict[str, Any]]) -> dict[str, Any]:
        if self._settings.use_openai:
            try:
                return self._synthesize_with_openai(message=message, intent=intent, tool_results=tool_results)
            except Exception as error:
                fallback = self._fallback_response(message=message, intent=intent, tool_results=tool_results)
                fallback["metadata"]["llmError"] = str(error)[:500]
                return fallback

        return self._fallback_response(message=message, intent=intent, tool_results=tool_results)

    def _synthesize_with_openai(
        self,
        *,
        message: str,
        intent: dict[str, Any],
        tool_results: list[dict[str, Any]],
    ) -> dict[str, Any]:
        from openai import OpenAI

        client = OpenAI(api_key=self._settings.openai_api_key)
        completion = client.chat.completions.create(
            model=self._settings.model,
            messages=[
                {
                    "role": "system",
                    "content": (
                        "Você é um agente técnico de investigação de logs. "
                        "Responda em português, cite evidências e indique próximos passos objetivos."
                    ),
                },
                {
                    "role": "user",
                    "content": (
                        f"Pergunta: {message}\n"
                        f"Intenção: {intent}\n"
                        f"Resultados das tools: {tool_results}\n"
                    ),
                },
            ],
            temperature=0.2,
        )
        content = completion.choices[0].message.content or ""

        return {
            "content": content,
            "metadata": {
                "provider": "openai",
                "model": self._settings.model,
                "usage": completion.usage.model_dump() if completion.usage else None,
            },
        }

    def _fallback_response(
        self,
        *,
        message: str,
        intent: dict[str, Any],
        tool_results: list[dict[str, Any]],
    ) -> dict[str, Any]:
        ok_tools = [tool for tool in tool_results if tool.get("status") == "ok"]
        failed_tools = [tool for tool in tool_results if tool.get("status") != "ok"]
        logs = _collect_logs(tool_results)
        docs = _collect_docs(tool_results)
        report = _collect_report(tool_results)

        lines = [f"Analisei a solicitação: {message}."]

        if logs:
            lines.append(f"Encontrei {len(logs)} logs relacionados. O mais recente é {logs[0].get('id')}.")
        else:
            lines.append("Não encontrei logs relacionados com os filtros inferidos.")

        if docs:
            lines.append(f"Também encontrei {len(docs)} trechos de documentação relevantes.")

        if report:
            lines.append(f"Hipótese principal: {report.get('hypothesis')}")
            next_steps = report.get("nextSteps") or []
            if next_steps:
                lines.append("Próximos passos: " + " ".join(f"{index + 1}. {step}" for index, step in enumerate(next_steps)))

        if failed_tools:
            lines.append(
                "Parte da investigação não foi concluída porque as seguintes tools falharam: "
                + ", ".join(tool.get("name", "unknown") for tool in failed_tools)
                + "."
            )

        return {
            "content": "\n\n".join(lines),
            "metadata": {
                "provider": "local-fallback",
                "toolCount": len(tool_results),
                "successfulToolCount": len(ok_tools),
                "failedToolCount": len(failed_tools),
                "intent": intent.get("action"),
            },
        }


def _collect_logs(tool_results: list[dict[str, Any]]) -> list[dict[str, Any]]:
    logs: list[dict[str, Any]] = []
    for tool in tool_results:
        if tool.get("name") in {"search_logs", "get_trace_timeline"} and isinstance(tool.get("output"), list):
            logs.extend(tool["output"])
    return logs


def _collect_docs(tool_results: list[dict[str, Any]]) -> list[dict[str, Any]]:
    for tool in tool_results:
        if tool.get("name") == "search_docs" and isinstance(tool.get("output"), dict):
            results = tool["output"].get("results")
            if isinstance(results, list):
                return results
    return []


def _collect_report(tool_results: list[dict[str, Any]]) -> dict[str, Any] | None:
    for tool in tool_results:
        if tool.get("name") == "generate_incident_report" and isinstance(tool.get("output"), dict):
            return tool["output"]
    return None
