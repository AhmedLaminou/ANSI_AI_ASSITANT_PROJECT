import io
import json
import math
import re
import subprocess
import tempfile
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


MIN_CHARS_PER_PAGE = 40


def ocr_available() -> bool:
    settings = get_settings()
    return bool(settings.tesseract_cmd) and Path(settings.tesseract_cmd).exists()


def ocr_page_images(content: bytes, page_numbers: list[int]) -> dict[int, str]:
    """Rasterises the given 1-based pages and runs local Tesseract on each.

    Everything stays on this machine: pypdfium2 renders, tesseract reads, nothing is uploaded.
    """
    import pypdfium2 as pdfium

    settings = get_settings()
    scale = settings.ocr_dpi / 72  # pypdfium2 works in points
    recognised: dict[int, str] = {}
    document = pdfium.PdfDocument(io.BytesIO(content))
    try:
        with tempfile.TemporaryDirectory(prefix="ansi-ocr-") as workspace:
            for page_number in page_numbers:
                image_path = Path(workspace) / f"page-{page_number}.png"
                page = document[page_number - 1]
                page.render(scale=scale).to_pil().save(image_path)
                completed = subprocess.run(
                    [
                        settings.tesseract_cmd,
                        str(image_path),
                        "stdout",
                        "-l",
                        settings.ocr_languages,
                        "--tessdata-dir",
                        settings.tessdata_dir,
                    ],
                    capture_output=True,
                    timeout=120,
                    check=False,
                )
                if completed.returncode == 0:
                    recognised[page_number] = completed.stdout.decode("utf-8", errors="replace")
    finally:
        document.close()
    return recognised


def extract_pages(filename: str, content: bytes) -> list[tuple[int, str]]:
    extension = Path(filename).suffix.lower()
    try:
        if extension == ".pdf":
            reader = PdfReader(io.BytesIO(content))
            pages = [(number, page.extract_text() or "") for number, page in enumerate(reader.pages, start=1)]
            # A scanned page yields (almost) no embedded text; fall back to local OCR.
            scanned = [number for number, text in pages if len(text.strip()) < MIN_CHARS_PER_PAGE]
            if scanned and ocr_available():
                settings = get_settings()
                recognised = ocr_page_images(content, scanned[: settings.ocr_max_pages])
                pages = [(number, recognised.get(number) or text) for number, text in pages]
            return pages
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


def search_similar_chunks(
    db, question_embedding: list[float], document_ids: list[int], limit: int
) -> list[tuple[float, "DocumentChunk"]]:
    """Top matching chunks, restricted to already-authorised documents.

    The candidate set is filtered by ACL *before* ranking on both engines: an
    unauthorised chunk is never a candidate, not merely dropped afterwards.
    """
    from sqlalchemy import select

    from .database import DocumentChunk, is_postgres

    if not document_ids:
        return []

    if is_postgres():
        from pgvector.sqlalchemy import Vector
        from sqlalchemy import Float, cast, literal

        from .database import EMBEDDING_DIMENSIONS

        # `<=>` is pgvector's cosine distance; the HNSW index answers exactly this
        # ordering. The bound parameter is cast so the operator resolves to vector.
        target = cast(literal(question_embedding), Vector(EMBEDDING_DIMENSIONS))
        distance = DocumentChunk.embedding.op("<=>", return_type=Float)(target)
        rows = db.execute(
            select(DocumentChunk, distance.label("distance"))
            .where(DocumentChunk.document_id.in_(document_ids))
            .order_by(distance)
            .limit(limit)
        ).all()
        return [(1.0 - row.distance, row[0]) for row in rows]

    ranked = [
        (cosine_similarity(question_embedding, chunk.embedding), chunk)
        for chunk in db.scalars(select(DocumentChunk).where(DocumentChunk.document_id.in_(document_ids))).all()
    ]
    ranked.sort(key=lambda result: result[0], reverse=True)
    return ranked[:limit]
