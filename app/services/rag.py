from __future__ import annotations

import json
from pathlib import Path

from sqlalchemy import delete, func, or_, select
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.models.cultural_ip import KnowledgeChunk, new_id
from app.services.embedding import cosine_similarity, create_embedding_service


class KnowledgeService:
    def __init__(self) -> None:
        self.settings = get_settings()
        self.embedder = create_embedding_service(self.settings)

    def ingest_directory(self, db: Session, reset: bool = False) -> tuple[int, list[str]]:
        if reset:
            db.execute(delete(KnowledgeChunk))
        base = Path(self.settings.knowledge_base_dir)
        base.mkdir(parents=True, exist_ok=True)
        inserted = 0
        sources: list[str] = []
        for path in sorted(base.glob("*")):
            if path.suffix.lower() not in {".json", ".md", ".txt"}:
                continue
            for item in self._load_items(path):
                for index, chunk_item in enumerate(self._split_item(item)):
                    content = chunk_item["content"]
                    embedding = self.embedder.embed(content)
                    chunk = KnowledgeChunk(
                        chunk_id=new_id("chunk"),
                        source_id=chunk_item["source_id"],
                        title=chunk_item["title"],
                        source_type=chunk_item.get("source_type", "local_knowledge"),
                        credibility_level=chunk_item.get("credibility_level", "C"),
                        content=content,
                        summary=chunk_item.get("summary", content[:180]),
                        tags=chunk_item.get("tags", []),
                        embedding=embedding,
                        relevance_hint=float(index),
                    )
                    db.add(chunk)
                    inserted += 1
                    sources.append(chunk_item["source_id"])
        db.commit()
        return inserted, sorted(set(sources))

    def list_chunks(self, db: Session, query: str | None = None, limit: int = 50) -> tuple[list[KnowledgeChunk], int]:
        stmt = select(KnowledgeChunk)
        count_stmt = select(func.count()).select_from(KnowledgeChunk)
        if query:
            pattern = f"%{query}%"
            predicate = or_(
                KnowledgeChunk.source_id.like(pattern),
                KnowledgeChunk.title.like(pattern),
                KnowledgeChunk.source_type.like(pattern),
                KnowledgeChunk.summary.like(pattern),
                KnowledgeChunk.content.like(pattern),
            )
            stmt = stmt.where(predicate)
            count_stmt = count_stmt.where(predicate)
        total = int(db.scalar(count_stmt) or 0)
        rows = list(
            db.scalars(
                stmt.order_by(KnowledgeChunk.updated_at.desc()).limit(max(min(limit, 200), 1))
            )
        )
        return rows, total

    def delete_source(self, db: Session, source_id: str) -> int:
        result = db.execute(delete(KnowledgeChunk).where(KnowledgeChunk.source_id == source_id))
        db.commit()
        return int(result.rowcount or 0)

    def search(self, db: Session, query_terms: list[str], limit: int = 8) -> list[dict]:
        rows = db.scalars(select(KnowledgeChunk)).all()
        query = " ".join(query_terms)
        query_embedding = self.embedder.embed(query)
        scored: list[tuple[float, KnowledgeChunk]] = []
        for row in rows:
            tag_text = " ".join(str(tag) for tag in row.tags)
            keyword_score = sum(
                0.18
                for term in query_terms
                if term and (term in row.content or term in row.title or term in tag_text)
            )
            semantic_score = cosine_similarity(query_embedding, row.embedding or [])
            credibility_bonus = {"A": 0.08, "B": 0.05, "C": 0.02}.get(row.credibility_level, 0.0)
            scored.append((keyword_score + semantic_score + credibility_bonus, row))
        scored.sort(key=lambda pair: pair[0], reverse=True)
        results = []
        for score, row in scored[:limit]:
            results.append(
                {
                    "source_id": row.source_id,
                    "chunk_id": row.chunk_id,
                    "title": row.title,
                    "source_type": row.source_type,
                    "summary": row.summary,
                    "matched_keywords": [
                        term for term in query_terms if term and (term in row.content or term in row.title)
                    ],
                    "credibility_level": row.credibility_level,
                    "relevance_score": round(max(score, 0.0), 3),
                }
            )
        return results

    def _load_items(self, path: Path) -> list[dict]:
        text = path.read_text(encoding="utf-8")
        if path.suffix.lower() == ".json":
            data = json.loads(text)
            if isinstance(data, dict):
                data = data.get("items", [data])
            return [self._normalize_item(item, path) for item in data]
        return [
            self._normalize_item(
                {
                    "source_id": path.stem,
                    "title": path.stem,
                    "content": text,
                    "summary": text[:180],
                    "tags": [],
                },
                path,
            )
        ]

    def _normalize_item(self, item: dict, path: Path) -> dict:
        title = item.get("title") or path.stem
        content = item.get("content") or item.get("summary") or title
        return {
            "source_id": item.get("source_id") or path.stem,
            "title": title,
            "source_type": item.get("source_type", "local_knowledge"),
            "credibility_level": item.get("credibility_level", "C"),
            "summary": item.get("summary", content[:180]),
            "content": content,
            "tags": item.get("tags", []),
        }

    def _split_item(self, item: dict) -> list[dict]:
        content = str(item["content"])
        max_size = max(self.settings.knowledge_chunk_size, 200)
        overlap = max(min(self.settings.knowledge_chunk_overlap, max_size // 3), 0)
        chunks = self._split_text(content, max_size=max_size, overlap=overlap)
        if len(chunks) == 1:
            return [item]
        split_items = []
        for index, chunk in enumerate(chunks, start=1):
            split_items.append(
                {
                    **item,
                    "source_id": item["source_id"],
                    "title": f"{item['title']} #{index}",
                    "content": chunk,
                    "summary": chunk[:180],
                }
            )
        return split_items

    def _split_text(self, text: str, max_size: int, overlap: int) -> list[str]:
        normalized = text.replace("\r\n", "\n").strip()
        if not normalized or len(normalized) <= max_size:
            return [normalized]
        paragraphs = [part.strip() for part in normalized.split("\n\n") if part.strip()]
        chunks: list[str] = []
        current = ""
        for paragraph in paragraphs:
            if len(paragraph) > max_size:
                if current:
                    chunks.append(current.strip())
                    current = ""
                chunks.extend(self._slice_long_text(paragraph, max_size=max_size, overlap=overlap))
                continue
            candidate = f"{current}\n\n{paragraph}".strip() if current else paragraph
            if len(candidate) <= max_size:
                current = candidate
            else:
                chunks.append(current.strip())
                current = paragraph
        if current:
            chunks.append(current.strip())
        return chunks or [normalized]

    def _slice_long_text(self, text: str, max_size: int, overlap: int) -> list[str]:
        chunks = []
        start = 0
        while start < len(text):
            end = min(start + max_size, len(text))
            chunks.append(text[start:end].strip())
            if end == len(text):
                break
            start = max(end - overlap, start + 1)
        return [chunk for chunk in chunks if chunk]
