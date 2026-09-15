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

import unicodedata
from typing import Any, Callable, TypedDict

import httpx
from langgraph.graph import END, START, StateGraph

from .config import get_settings
from .tools import find_tool


# A greeting is not a documentary question. Sending "salut" through the retrieval
# pipeline produces "information non trouvée", which reads as a broken assistant.
SOCIAL_PATTERNS = (
    "salut", "bonjour", "bonsoir", "coucou", "hello", "hi", "hey", "yo",
    "ca va", "comment ca va", "comment vas tu", "comment allez vous",
    "merci", "merci beaucoup", "ok", "d accord", "au revoir", "bonne journee",
    "bonne soiree", "a bientot", "bye",
    "qui es tu", "qui etes vous", "que sais tu faire", "que peux tu faire",
    "tu sers a quoi", "aide", "help", "test",
)
MAX_TRAILING_WORDS = 2


def normalise(text: str) -> str:
    """Lowercase, strip accents and punctuation, collapse spaces."""
    decomposed = unicodedata.normalize("NFD", text.lower())
    without_accents = "".join(c for c in decomposed if unicodedata.category(c) != "Mn")
    cleaned = "".join(c if c.isalnum() or c.isspace() else " " for c in without_accents)
    return " ".join(cleaned.split())


def classify_intent(message: str) -> str:
    """"social" for greetings and small talk, "documentary" for anything else.

    Deliberately a fast rule rather than a model call: classification must not add
    twenty seconds to every question just to recognise "bonjour". A greeting followed
    by a real question stays documentary — only a trailing courtesy is tolerated.
    """
    text = normalise(message)
    if not text:
        return "social"
    for pattern in SOCIAL_PATTERNS:
        if text == pattern:
            return "social"
        if text.startswith(pattern + " "):
            remainder = text[len(pattern):].split()
            if len(remainder) <= MAX_TRAILING_WORDS:
                return "social"
    return "documentary"


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
    intent: str  # "social" | "tool" | "documentary"
    outcome: str  # "answer" | "refuse" | "social" | "tool"
    tool_answer: str


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
    run_tool: Callable[[str], str | None] | None = None,
):
    """`retrieve` and `run_tool` are injected so the graph stays free of database
    and ACL concerns: both already apply the caller's permissions."""

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

    def route_node(state: AssistantState) -> AssistantState:
        question = state["question"]
        if classify_intent(question) == "social":
            return {"intent": "social"}
        # A question about the system itself is answered from the database, not
        # from documents. The match is deterministic: the model never chooses.
        if run_tool is not None and find_tool(question) is not None:
            return {"intent": "tool"}
        return {"intent": "documentary"}

    def route_intent(state: AssistantState) -> str:
        intent = state.get("intent")
        if intent == "social":
            return "social"
        if intent == "tool":
            return "tool"
        return "retrieve"

    def social_node(state: AssistantState) -> AssistantState:
        return {"outcome": "social"}

    def tool_node(state: AssistantState) -> AssistantState:
        answer = run_tool(state["question"]) if run_tool else None
        if answer is None:
            # Refused (wrong role) or no longer matching: fall back to documents.
            return {"intent": "documentary"}
        return {"outcome": "tool", "tool_answer": answer}

    def after_tool(state: AssistantState) -> str:
        return "retrieve" if state.get("outcome") != "tool" else "done"

    graph = StateGraph(AssistantState)
    graph.add_node("route", route_node)
    graph.add_node("social", social_node)
    graph.add_node("tool", tool_node)
    graph.add_node("retrieve", retrieve_node)
    graph.add_node("grade", grade_node)
    graph.add_node("rewrite", rewrite_node)
    graph.add_node("answer", answer_node)
    graph.add_node("refuse", refuse_node)

    graph.add_edge(START, "route")
    graph.add_conditional_edges(
        "route", route_intent, {"social": "social", "tool": "tool", "retrieve": "retrieve"}
    )
    graph.add_edge("social", END)
    graph.add_conditional_edges("tool", after_tool, {"retrieve": "retrieve", "done": END})
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
