import io
import json
import math
import re
from dataclasses import dataclass
from pathlib import Path

import httpx
from docx import Document as WordDocument
from pypdf import PdfReader

from .config import BACKEND_DIR, get_settings


DOCUMENT_STORAGE_DIR = BACKEND_DIR / "data" / "documents"
SUPPORTED_EXTENSIONS = {".pdf", ".docx", ".txt", ".md"}
CHUNK_SIZE = 900
CHUNK_OVERLAP = 150


class RagError(Exception):
    pass


@dataclass
class ParsedChunk:
    content: str
    page_number: int


def extract_pages(filename: str, content: bytes) -> list[tuple[int, str]]:
    extension = Path(filename).suffix.lower()
    try:
        if extension == ".pdf":
            reader = PdfReader(io.BytesIO(content))
            return [(number, page.extract_text() or "") for number, page in enumerate(reader.pages, start=1)]
        if extension == ".docx":
            document = WordDocument(io.BytesIO(content))
            return [(1, "\n".join(paragraph.text for paragraph in document.paragraphs))]
        if extension in {".txt", ".md"}:
            return [(1, content.decode("utf-8", errors="replace"))]
    except Exception as exc:
        raise RagError("Le fichier ne peut pas être lu. Vérifiez son format et son contenu.") from exc
    raise RagError("Format non pris en charge. Utilisez PDF, DOCX, TXT ou Markdown.")


def chunk_pages(pages: list[tuple[int, str]]) -> list[ParsedChunk]:
    chunks: list[ParsedChunk] = []
    for page_number, raw_text in pages:
        text = re.sub(r"\s+", " ", raw_text).strip()
        if not text:
            continue
        start = 0
        while start < len(text):
            end = min(len(text), start + CHUNK_SIZE)
            if end < len(text):
                last_break = max(text.rfind(". ", start, end), text.rfind(" ", start, end))
                if last_break > start + CHUNK_SIZE // 2:
                    end = last_break + 1
            part = text[start:end].strip()
            if part:
                chunks.append(ParsedChunk(content=part, page_number=page_number))
            if end >= len(text):
                break
            start = max(end - CHUNK_OVERLAP, start + 1)
    return chunks


async def embed_texts(texts: list[str]) -> list[list[float]]:
    settings = get_settings()
    try:
        async with httpx.AsyncClient(timeout=120) as client:
            response = await client.post(
                f"{settings.ollama_base_url}/api/embed",
                json={"model": settings.ollama_embedding_model, "input": texts, "keep_alive": "10m"},
            )
            response.raise_for_status()
    except httpx.HTTPError as exc:
        raise RagError("Le modèle d'embeddings local est indisponible.") from exc
    embeddings = response.json().get("embeddings")
    if not embeddings or len(embeddings) != len(texts):
        raise RagError("Le modèle d'embeddings a retourné une réponse invalide.")
    return embeddings


def serialize_embedding(embedding: list[float]) -> str:
    return json.dumps(embedding, separators=(",", ":"))


def cosine_similarity(left: list[float], right: list[float]) -> float:
    numerator = sum(a * b for a, b in zip(left, right))
    denominator = math.sqrt(sum(a * a for a in left)) * math.sqrt(sum(b * b for b in right))
    return numerator / denominator if denominator else 0.0


def parse_allowed_roles(value: str) -> set[str]:
    return {role.strip() for role in value.split(",") if role.strip()}
