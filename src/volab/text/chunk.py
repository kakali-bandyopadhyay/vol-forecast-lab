"""Paragraph-aware chunking for the Step 10b text index."""

from __future__ import annotations

import hashlib
import re
from typing import Any

import tiktoken

from volab.text.extract import EXTRACTOR_VERSION

_ENCODING = tiktoken.get_encoding("cl100k_base")
_SENTENCE = re.compile(r"(?<=[.?!])\s+(?=[A-Z])")


def _tokens(text: str) -> list[int]:
    return _ENCODING.encode(text)


def _token_split(text: str, max_tokens: int) -> list[str]:
    tokens = _tokens(text)
    if len(tokens) <= max_tokens:
        return [text]
    pieces = []
    for start in range(0, len(tokens), max_tokens):
        piece = _ENCODING.decode(tokens[start:start + max_tokens]).strip()
        if piece:
            pieces.append(piece)
    return pieces


def _split_paragraph(text: str, max_tokens: int) -> list[str]:
    """Split an oversized paragraph at sentence ends, then at token boundaries."""
    if len(_tokens(text)) <= max_tokens:
        return [text]

    pieces: list[str] = []
    current: list[str] = []
    for sentence in _SENTENCE.split(text):
        if len(_tokens(sentence)) > max_tokens:
            if current:
                pieces.append(" ".join(current))
                current = []
            pieces.extend(_token_split(sentence, max_tokens))
            continue
        candidate = " ".join([*current, sentence])
        if current and len(_tokens(candidate)) > max_tokens:
            pieces.append(" ".join(current))
            current = [sentence]
        else:
            current.append(sentence)
    if current:
        pieces.append(" ".join(current))
    return pieces


def chunk_document(document: dict[str, Any], *, max_tokens: int = 300) -> list[dict[str, Any]]:
    """Greedily merge consecutive paragraphs into chunks of at most `max_tokens` tokens."""
    if max_tokens < 1:
        raise ValueError("max_tokens must be positive")
    paragraphs = document.get("paragraphs") or []
    if not paragraphs:
        return []

    pieces: list[tuple[str, Any]] = []
    for paragraph in paragraphs:
        text = str(paragraph["text"]).strip()
        if not text:
            continue
        for piece in _split_paragraph(text, max_tokens):
            pieces.append((piece, paragraph.get("page")))

    chunks: list[dict[str, Any]] = []
    current: list[str] = []
    current_page = None
    for text, page in pieces:
        if not current:
            current = [text]
            current_page = page
            continue
        candidate = "\n\n".join([*current, text])
        if len(_tokens(candidate)) <= max_tokens:
            current.append(text)
        else:
            chunks.append(_make_chunk(document, len(chunks), current, current_page))
            current = [text]
            current_page = page
    if current:
        chunks.append(_make_chunk(document, len(chunks), current, current_page))
    return chunks


def _make_chunk(
    document: dict[str, Any], ordinal: int, pieces: list[str], page: Any
) -> dict[str, Any]:
    text = "\n\n".join(pieces)
    return {
        "chunk_id": f"{document['doc_id']}-{ordinal:04d}",
        "doc_id": document["doc_id"],
        "doc_type": document["doc_type"],
        "event_date": document["event_date"],
        "ord": ordinal,
        "page": page,
        "text": text,
        "n_tokens": len(_tokens(text)),
        "content_hash": hashlib.sha256(
            f"{text}|{EXTRACTOR_VERSION}".encode("utf-8")
        ).hexdigest(),
    }
