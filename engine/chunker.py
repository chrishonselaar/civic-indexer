"""Passage chunking for the search projection.

Ported near-verbatim from wob-search-mcp/shared/document_chunker.py
(paragraph-grouping with sentence-split fallback and overlap). Chunks are
derived artifacts: they carry CHUNKER_VERSION and are replaced wholesale on
re-projection, never migrated (envelope-desk-checks.md Q5).
"""

import re
from dataclasses import dataclass

CHUNKER_VERSION = "1"


@dataclass
class Chunk:
    chunk_id: str
    parent_urn: str
    text: str
    number: int
    total: int
    size_words: int


class DocumentChunker:
    def __init__(
        self,
        target_chunk_size: int = 750,
        max_chunk_size: int = 1200,
        min_chunk_size: int = 200,
        overlap_size: int = 100,
    ):
        self.target_chunk_size = target_chunk_size
        self.max_chunk_size = max_chunk_size
        self.min_chunk_size = min_chunk_size
        self.overlap_size = overlap_size

    def chunk_document(self, text: str, parent_urn: str) -> list[Chunk]:
        if not text or not text.strip():
            return []
        paragraphs = [p.strip() for p in re.split(r"\n\n+", text) if p.strip()]
        raw = self._create_chunks_from_paragraphs(paragraphs)
        return [
            Chunk(
                chunk_id=f"{parent_urn}_chunk_{i:03d}",
                parent_urn=parent_urn,
                text=chunk_text,
                number=i,
                total=len(raw),
                size_words=len(chunk_text.split()),
            )
            for i, chunk_text in enumerate(raw, 1)
        ]

    def _create_chunks_from_paragraphs(self, paragraphs: list[str]) -> list[str]:
        chunks: list[str] = []
        current: list[str] = []
        current_words = 0
        previous_tail: list[str] = []

        for para in paragraphs:
            para_words = len(para.split())

            if para_words > self.max_chunk_size:
                if current:
                    chunks.append("\n\n".join(current))
                    previous_tail = current[-2:]
                    current = []
                    current_words = 0
                sentences = re.split(r"(?<=[.!?])\s+", para)
                sub: list[str] = []
                sub_words = 0
                for sentence in sentences:
                    n = len(sentence.split())
                    if sub_words + n > self.max_chunk_size and sub:
                        chunks.append(" ".join(sub))
                        sub = []
                        sub_words = 0
                    sub.append(sentence)
                    sub_words += n
                if sub:
                    chunks.append(" ".join(sub))
                continue

            exceeds_target = (current_words + para_words) > self.target_chunk_size
            exceeds_max = (current_words + para_words) > self.max_chunk_size

            if exceeds_max or (exceeds_target and current_words >= self.min_chunk_size):
                if current:
                    chunks.append("\n\n".join(current))
                    previous_tail = current[-2:]
                overlap: list[str] = []
                overlap_words = 0
                for prev in reversed(previous_tail):
                    n = len(prev.split())
                    if overlap_words + n <= self.overlap_size:
                        overlap.insert(0, prev)
                        overlap_words += n
                    else:
                        break
                current = overlap + [para]
                current_words = overlap_words + para_words
            else:
                current.append(para)
                current_words += para_words

        if current:
            chunks.append("\n\n".join(current))
        return chunks
