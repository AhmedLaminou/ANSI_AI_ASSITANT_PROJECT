"""Parcours de décision de l'assistant, orchestré avec LangGraph.

Le chemin n'est pas linéaire. Quand la recherche ne ramène rien d'assez proche, il ne
faut pas répondre « je ne sais pas » immédiatement : la question est souvent formulée
avec un vocabulaire différent de celui du document. Le graphe reformule alors la
question et retente une fois avant de renoncer.

    retrieve ──► grade ─┬─ pertinent ──────────► (contexte prêt, génération)
        ▲               ├─ faible, 1er essai ──► rewrite ──┐
        └───────────────┴─ faible, déjà retenté ► refuse   │
                        └──────────────────────────────────┘

La génération finale n'est pas un nœud : elle reste en streaming côté endpoint, pour
que l'utilisateur voie la réponse s'écrire. Le graphe décide *quoi* générer.
"""

from typing import Annotated, Any, Callable, TypedDict

import httpx
from langgraph.graph import END, START, StateGraph

from .config import get_settings


REWRITE_SYSTEM = (
    "Tu reformules une question pour une recherche documentaire interne. "
    "Réécris-la avec des termes administratifs explicites et des synonymes probables du document. "
    "Réponds uniquement par la question reformulée, sans commentaire ni guillemets."
)


class AssistantState(TypedDict, total=False):
    question: str
    search_question: str
    attempts: int
    chunks: list[Any]
    best_score: float
    rewritten: bool
    outcome: str  # "answer" | "refuse"


def strip_reasoning(text: str) -> str:
    return text.rsplit("</think>", 1)[-1].strip() if "</think>" in text else text.strip()


async def rewrite_question(question: str) -> str:
    """Asks the local model for a better search phrasing. Falls back to the original."""
    settings = get_settings()
    body = {
        "model": settings.ollama_chat_model,
        "stream": False,
        "think": False,
        "messages": [
            {"role": "system", "content": REWRITE_SYSTEM},
            {"role": "user", "content": question},
        ],
        "options": {"num_ctx": 2048, "temperature": 0.3},
        "keep_alive": "10m",
    }
    try:
        async with httpx.AsyncClient(timeout=120) as client:
            response = await client.post(f"{settings.ollama_base_url}/api/chat", json=body)
            response.raise_for_status()
        candidate = strip_reasoning(response.json().get("message", {}).get("content", ""))
    except (httpx.HTTPError, ValueError):
        return question
    candidate = candidate.strip().strip('"').splitlines()[0] if candidate.strip() else ""
    return candidate or question


def build_assistant_graph(
    retrieve: Callable,
    relevance_threshold: float,
    max_attempts: int = 2,
):
    """`retrieve` is injected so the graph stays free of database and ACL concerns."""

    async def retrieve_node(state: AssistantState) -> AssistantState:
        chunks = await retrieve(state["search_question"])
        return {
            "chunks": chunks,
            "best_score": chunks[0][0] if chunks else 0.0,
            "attempts": state.get("attempts", 0) + 1,
        }

    def grade_node(state: AssistantState) -> AssistantState:
        return {}

    def route_after_grade(state: AssistantState) -> str:
        if state.get("chunks") and state.get("best_score", 0.0) >= relevance_threshold:
            return "answer"
        if state.get("attempts", 0) < max_attempts:
            return "rewrite"
        return "refuse"

    async def rewrite_node(state: AssistantState) -> AssistantState:
        return {"search_question": await rewrite_question(state["question"]), "rewritten": True}

    def answer_node(state: AssistantState) -> AssistantState:
        return {"outcome": "answer"}

    def refuse_node(state: AssistantState) -> AssistantState:
        return {"outcome": "refuse"}

    graph = StateGraph(AssistantState)
    graph.add_node("retrieve", retrieve_node)
    graph.add_node("grade", grade_node)
    graph.add_node("rewrite", rewrite_node)
    graph.add_node("answer", answer_node)
    graph.add_node("refuse", refuse_node)

    graph.add_edge(START, "retrieve")
    graph.add_edge("retrieve", "grade")
    graph.add_conditional_edges(
        "grade",
        route_after_grade,
        {"answer": "answer", "rewrite": "rewrite", "refuse": "refuse"},
    )
    graph.add_edge("rewrite", "retrieve")  # the cycle, bounded by max_attempts
    graph.add_edge("answer", END)
    graph.add_edge("refuse", END)
    return graph.compile()
