from typing import Any, TypedDict

from langgraph.graph import END, StateGraph

from app.intent import InvestigationIntent, interpret_message
from app.llm import ResponseSynthesizer
from app.repositories import PostgresRepository
from app.schemas import ChatRequest, ChatResponse, ToolCall
from app.tools import ToolRunner


class AgentState(TypedDict, total=False):
    request: ChatRequest
    history: list[dict[str, Any]]
    intent: InvestigationIntent
    tool_results: list[dict[str, Any]]
    context: dict[str, Any]
    response: ChatResponse


class InvestigationAgent:
    def __init__(self, postgres: PostgresRepository, tools: ToolRunner, synthesizer: ResponseSynthesizer) -> None:
        self._postgres = postgres
        self._tools = tools
        self._synthesizer = synthesizer
        self._graph = self._build_graph()

    def run(self, request: ChatRequest) -> ChatResponse:
        state = self._graph.invoke({"request": request})
        return state["response"]

    def _build_graph(self):
        graph = StateGraph(AgentState)
        graph.add_node("load_context", self._load_context)
        graph.add_node("interpret", self._interpret)
        graph.add_node("execute_tools", self._execute_tools)
        graph.add_node("respond", self._respond)
        graph.set_entry_point("load_context")
        graph.add_edge("load_context", "interpret")
        graph.add_edge("interpret", "execute_tools")
        graph.add_edge("execute_tools", "respond")
        graph.add_edge("respond", END)
        return graph.compile()

    def _load_context(self, state: AgentState) -> AgentState:
        request = state["request"]
        try:
            history = self._postgres.fetch_conversation_history(request.conversation_id)
        except Exception:
            history = []
        return {**state, "history": history}

    def _interpret(self, state: AgentState) -> AgentState:
        request = state["request"]
        intent = interpret_message(request.message, state.get("history", []))
        return {**state, "intent": intent}

    def _execute_tools(self, state: AgentState) -> AgentState:
        intent = state["intent"]
        context: dict[str, Any] = {
            "logs": [],
            "errorGroups": [],
            "docs": [],
            "report": None,
            "githubIssue": None,
        }
        results: list[dict[str, Any]] = []

        for tool_name in intent.selected_tools:
            result = self._tools.run(tool_name, intent, context)
            results.append(result)
            _merge_tool_output(context, result)

        return {**state, "tool_results": results, "context": context}

    def _respond(self, state: AgentState) -> AgentState:
        request = state["request"]
        intent = state["intent"]
        tool_results = state.get("tool_results", [])
        context = state.get("context", {})
        synthesized = self._synthesizer.synthesize(
            message=request.message,
            intent={
                "action": intent.action,
                "selectedTools": intent.selected_tools,
                "service": intent.service,
                "environment": intent.environment,
                "level": intent.level,
            },
            tool_results=tool_results,
        )
        response = ChatResponse(
            content=synthesized["content"],
            evidence=_build_evidence(tool_results),
            logs=context.get("logs", []),
            tools=[ToolCall(**tool) for tool in tool_results],
            metadata={
                **synthesized.get("metadata", {}),
                "conversationId": request.conversation_id,
                "userId": request.user_id,
                "state": {
                    "historyMessages": len(state.get("history", [])),
                    "selectedTools": intent.selected_tools,
                },
            },
        )
        return {**state, "response": response}


def _merge_tool_output(context: dict[str, Any], result: dict[str, Any]) -> None:
    if result.get("status") != "ok":
        return

    name = result.get("name")
    output = result.get("output")

    if name == "search_logs" and isinstance(output, list):
        context["logs"].extend(output)
    elif name == "get_trace_timeline" and isinstance(output, list):
        context["logs"].extend(output)
    elif name == "aggregate_errors" and isinstance(output, list):
        context["errorGroups"] = output
    elif name == "search_docs" and isinstance(output, dict):
        context["docs"] = output.get("results", [])
    elif name == "generate_incident_report" and isinstance(output, dict):
        context["report"] = output
    elif name == "create_github_issue" and isinstance(output, dict):
        context["githubIssue"] = output


def _build_evidence(tool_results: list[dict[str, Any]]) -> list[dict[str, Any]]:
    evidence: list[dict[str, Any]] = []

    for tool in tool_results:
        if tool.get("status") != "ok":
            evidence.append({"type": "tool_error", "tool": tool.get("name"), "error": tool.get("error")})
            continue

        if tool.get("name") == "search_logs":
            evidence.append({"type": "logs", "count": len(tool.get("output") or [])})
        elif tool.get("name") == "aggregate_errors":
            evidence.append({"type": "error_groups", "count": len(tool.get("output") or [])})
        elif tool.get("name") == "search_docs":
            evidence.append({"type": "docs", "count": len((tool.get("output") or {}).get("results", []))})
        elif tool.get("name") == "generate_incident_report":
            evidence.append({"type": "incident_report", "report": tool.get("output")})
        elif tool.get("name") == "create_github_issue":
            evidence.append({"type": "github_issue", "issue": tool.get("output")})

    return evidence
