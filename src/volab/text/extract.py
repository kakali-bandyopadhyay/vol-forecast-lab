"""Extract FOMC statements and minutes into the Step 10b document record."""

from __future__ import annotations

import re
import unicodedata
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any

import pymupdf
from bs4 import BeautifulSoup

EXTRACTOR_VERSION = "1.0"

_TERMINAL = re.compile(r"[.:;?]$")
_PAGE_NUMBER = re.compile(r"^(?:page\s+)?\d+$", re.IGNORECASE)
_UNDERSCORES = re.compile(r"^_{5,}$")
_DATE_LINE = re.compile(r"^[A-Z][a-z]+ \d{1,2}(?:[–-]\d{1,2})?, \d{4}$")
_MINUTES_TITLE = re.compile(r"^Minutes of the (?:Federal Open Market Committee|Meeting of .*)$")
_TALLY = re.compile(r"\bby a\s+(\d+)\s*[–-]\s*(\d+)\s+vote\b", re.IGNORECASE)
_NAME = re.compile(r"^[A-Z][\w.'’\-]*(?: [A-Z][\w.'’\-]*)+$")
_RUNNING_HEADER = re.compile(
    r"^Minutes of the Federal Open Market Committee\s+\d+$", re.IGNORECASE
)
_STANDARD_HEADINGS = (
    "Committee Policy Action",
    "Staff Review of the Economic Situation",
    "Staff Review of the Financial Situation",
    "Developments in Financial Markets",
)


class ExtractionError(RuntimeError):
    """A source document does not have the structure the extractor requires."""


def _normalise(text: str) -> str:
    """NFKC, soft-hyphen removal, hyphen line-break joining and whitespace collapse."""
    text = unicodedata.normalize("NFKC", text)
    text = text.replace("\u00ad", "")
    text = re.sub(r"-\s*\n\s*(?=[a-z])", "", text)
    return re.sub(r"\s+", " ", text).strip()


def _event_date(value: str) -> date:
    for fmt in ("%Y%m%d", "%Y-%m-%d"):
        try:
            return datetime.strptime(str(value), fmt).date()
        except ValueError:
            continue
    raise ValueError("event_date must be YYYYMMDD or YYYY-MM-DD")


def _statement_drop(text: str) -> bool:
    lowered = text.lower()
    if lowered.startswith(("implementation note", "for media inquiries", "last update:")):
        return True
    if lowered.startswith(("for release", "for immediate release")):
        return True
    return bool(_DATE_LINE.fullmatch(text))


def _validated_names(raw: str, context: str) -> list[str]:
    # Titles can be followed by a comma, semicolon, period or the end of the clause.
    raw = re.sub(
        r",\s*(?:Vice Chair(?:man)?|Chair(?:man)?)(?=,|;|\.|$)",
        "",
        raw,
        flags=re.I,
    )
    names: list[str] = []
    for clause in re.split(r";\s*", raw):
        clause = clause.strip()
        if not clause:
            continue
        clause = re.sub(
            r",\s*(?:Vice Chair(?:man)?|Chair(?:man)?)$", "", clause, flags=re.I
        ).strip()
        for part in re.split(r",\s*and\s+|\s+and\s+|,\s*", clause):
            name = part.strip().rstrip(".").strip()
            if name.lower().startswith("and "):
                name = name[4:].strip()
            if not name:
                continue
            if not _NAME.fullmatch(name):
                raise ExtractionError(f"unrecognised {context} vote name: {name!r}")
            names.append(name)
    return names


def _against_split(body: str) -> tuple[str, str | None]:
    candidates: list[tuple[int, re.Match[str], str]] = []
    for pattern, kind in (
        (r",\s*who\b", "who"),
        (r"\swho\b", "who"),
        (r",\s*each of whom\b", "each"),
    ):
        match = re.search(pattern, body, flags=re.I)
        if match:
            candidates.append((match.start(), match, kind))
    for match in re.finditer(r"\.\s+(?=[A-Z])", body):
        if re.search(r"\b[A-Z]$", body[: match.start()]):
            continue
        candidates.append((match.start(), match, "sentence"))
    if not candidates:
        return body.strip().rstrip("."), None

    _start, match, kind = min(candidates, key=lambda item: item[0])
    names_text = body[: match.start()].strip().rstrip(".").strip()
    if kind == "sentence":
        dissent_text = body[match.end():].strip()
    else:
        dissent_text = body[match.start():].strip()
        if dissent_text.startswith(","):
            dissent_text = dissent_text[1:].strip()
    return names_text, dissent_text or None


def _vote_record(paragraphs: list[str]) -> tuple[list[str], list[str], str | None]:
    for_parts: list[str] = []
    against_parts: list[str] = []
    for text in paragraphs:
        for_match = re.search(r"\bVoting for\b", text, flags=re.I)
        against_match = re.search(r"\bVoting against\b", text, flags=re.I)
        if for_match and against_match and for_match.start() < against_match.start():
            for_parts.append(text[for_match.start(): against_match.start()])
            against_parts.append(text[against_match.start():])
        elif for_match:
            for_parts.append(text[for_match.start():])
        elif against_match:
            against_parts.append(text[against_match.start():])

    for_text = " ".join(for_parts).strip()
    against_text = " ".join(against_parts).strip()
    for_match = re.search(
        r"Voting for.*?\b(?:were|was)\b[:\s]*(.*)", for_text, flags=re.I | re.S
    )
    votes_for = (
        _validated_names(_against_split(for_match.group(1).strip())[0], "for")
        if for_match
        else []
    )

    votes_against: list[str] = []
    dissent_text: str | None = None
    against_match = re.search(
        r"Voting against.*?\b(?:were|was)\b[:\s]*(.*)", against_text, flags=re.I | re.S
    )
    if against_match:
        names_text, dissent_text = _against_split(against_match.group(1).strip())
        votes_against = _validated_names(names_text, "against") if names_text else []
    return votes_for, votes_against, dissent_text


def _extract_statement(
    source_path: Path,
) -> tuple[str, list[dict[str, Any]], list[str], list[str], str | None, str | None]:
    soup = BeautifulSoup(source_path.read_text(encoding="utf-8", errors="replace"), "html.parser")
    article = soup.find("div", id="article")
    if article is None:
        raise ExtractionError("statement has no div#article")

    texts: list[str] = []
    for paragraph in article.find_all("p"):
        text = _normalise(paragraph.get_text(" ", strip=True))
        if text and not _statement_drop(text):
            texts.append(text)
    if not texts:
        raise ExtractionError("statement has no body paragraphs")

    votes_for, votes_against, dissent_text = _vote_record(texts)
    tally = _TALLY.search("\n".join(texts))
    vote_tally = f"{tally.group(1)}-{tally.group(2)}" if tally else None
    paragraphs = [{"text": text, "page": None} for text in texts]
    return (
        "\n\n".join(texts),
        paragraphs,
        votes_for,
        votes_against,
        dissent_text,
        vote_tally,
    )


def _header_line(text: str) -> bool:
    lowered = text.lower().strip()
    return bool(
        _UNDERSCORES.fullmatch(text)
        or _PAGE_NUMBER.fullmatch(text)
        or lowered in {"federal open market committee", "minutes of the federal open market committee"}
        or _MINUTES_TITLE.fullmatch(text)
        or _DATE_LINE.fullmatch(text)
        or _RUNNING_HEADER.fullmatch(text)
    )


def _ordered_blocks(page) -> list[Any]:
    """Order blocks down each column, using full-width blocks as band breaks."""
    width = float(page.rect.width)
    blocks = [block for block in page.get_text("blocks") if block[6] == 0]
    full_width = {
        id(block): block
        for block in blocks
        if (float(block[2]) - float(block[0])) > 0.6 * width
    }

    def band(block) -> int:
        return sum(1 for full in full_width.values() if float(full[1]) < float(block[1]))

    def column(block) -> int:
        return 0 if float(block[0]) < width / 2.0 else 1

    return sorted(
        blocks,
        key=lambda block: (
            band(block),
            0 if id(block) in full_width else column(block),
            float(block[1]),
            float(block[0]),
        ),
    )


def _title_like(text: str) -> bool:
    """A heading/chart-label candidate: at least two words, short, no terminal stop."""
    words = re.findall(r"[A-Za-z][A-Za-z'’\-]*", text)
    return 2 <= len(words) <= 12 and not _TERMINAL.search(text)


def _body_window(texts: list[str], start: int, page_numbers: list[int] | None = None) -> bool:
    """Look ahead through split body blocks for ≥25 words ending in body prose."""
    parts: list[str] = []
    first = True
    start_page = page_numbers[start] if page_numbers else None
    for index in range(start, min(len(texts), start + 12)):
        text = texts[index]
        if page_numbers is not None and page_numbers[index] != start_page and text:
            if first and not text[:1].islower():
                return False
        if not text:
            continue
        if first:
            if re.match(r"^\d", text):
                return False
            if re.match(r"^(?:NOTE|Note|Source):", text):
                return False
            if _title_like(text):
                return False
            first_tokens = re.findall(r"\S+", text)
            alphabetic = sum(1 for token in first_tokens if any(char.isalpha() for char in token))
            if not first_tokens or alphabetic / len(first_tokens) < 0.5:
                return False
            first = False
        parts.append(text)
        joined = " ".join(parts)
        tokens = re.findall(r"\S+", joined)
        if len(tokens) < 25:
            continue
        alphabetic = sum(1 for token in tokens if any(char.isalpha() for char in token))
        if alphabetic / len(tokens) < 0.5:
            return False
        if _TERMINAL.search(text):
            return True
        if index + 1 < len(texts) and texts[index + 1][:1].islower():
            return True
    return False


def _title_allowed(
    texts: list[str], index: int, page_numbers: list[int] | None = None
) -> bool:
    """A title survives only when the next raw blocks begin body prose."""
    if index + 1 >= len(texts):
        return False
    return _body_window(texts, index + 1, page_numbers)


def _noise_block(text: str) -> bool:
    """Drop short table/chart fragments: <5 words or <50% alphabetic tokens."""
    tokens = re.findall(r"\S+", text)
    if len(tokens) < 5:
        return True
    alphabetic = sum(1 for token in tokens if any(char.isalpha() for char in token))
    return alphabetic / len(tokens) < 0.5


def _attendee_block(text: str, page_number: int) -> bool:
    """Drop the page 1–2 attendee lists and footnote blocks."""
    if page_number > 2:
        return False
    if any(heading in text for heading in _STANDARD_HEADINGS):
        return False
    if re.match(r"^\d+\s", text):
        return True
    words = re.findall(r"[A-Za-z][A-Za-z'’\-]*", text)
    if not words:
        return True
    capitals = sum(1 for word in words if word[0].isupper())
    return capitals / len(words) >= 0.5 and not _TERMINAL.search(text)


def _join_raw(raw: list[tuple[int, str]]) -> list[dict[str, Any]]:
    """Join a split paragraph at any block boundary, keeping the start page."""
    paragraphs: list[dict[str, Any]] = []
    for page_number, text in raw:
        if paragraphs:
            previous = paragraphs[-1]
            if not _TERMINAL.search(previous["text"]) and text[:1].islower():
                if previous["text"].endswith("-"):
                    previous["text"] = previous["text"][:-1] + text
                else:
                    previous["text"] = f"{previous['text']} {text}"
                continue
        paragraphs.append({"text": text, "page": page_number})
    return paragraphs


def _extract_minutes(source_path: Path) -> tuple[str, list[dict[str, Any]], int, int]:
    document = pymupdf.open(source_path)
    try:
        pages: list[list[Any]] = [_ordered_blocks(page) for page in document]

        signatures: list[list[str]] = []
        for blocks in pages:
            signatures.append(
                [
                    re.sub(r"\d", "#", _normalise(block[4]))
                    for block in blocks
                ]
            )
        threshold = max(2, (len(pages) + 1) // 2)

        def repeated(which: int) -> set[str]:
            counts: dict[str, int] = {}
            for page_signatures in signatures:
                if not page_signatures:
                    continue
                value = page_signatures[which]
                counts[value] = counts.get(value, 0) + 1
            return {value for value, count in counts.items() if count >= threshold}

        drop_first = repeated(0)
        drop_last = repeated(-1)

        entries: list[tuple[int, str, str]] = []
        for page_number, (blocks, page_signatures) in enumerate(
            zip(pages, signatures), start=1
        ):
            for block, signature in zip(blocks, page_signatures):
                lines = []
                for line in block[4].splitlines():
                    line = line.strip()
                    if not line or _PAGE_NUMBER.fullmatch(line) or _header_line(line):
                        continue
                    lines.append(line)
                text = _normalise("\n".join(lines)) if lines else ""
                entries.append((page_number, signature, text))
        texts = [text for _, _, text in entries]
        page_numbers = [page for page, _, _ in entries]

        raw: list[tuple[int, str]] = []
        blocks_dropped = 0
        for index, (page_number, signature, text) in enumerate(entries):
            if signature in drop_first or signature in drop_last:
                continue
            if not text:
                continue
            if _title_like(text):
                if _title_allowed(texts, index, page_numbers):
                    raw.append((page_number, text))
                else:
                    blocks_dropped += 1
                continue
            if _attendee_block(text, page_number) or _noise_block(text):
                blocks_dropped += 1
                continue
            raw.append((page_number, text))

        paragraphs = _join_raw(raw)

        return (
            "\n\n".join(item["text"] for item in paragraphs),
            paragraphs,
            len(pages),
            blocks_dropped,
        )
    finally:
        document.close()


def extract_document(
    source_path: Path,
    *,
    event_date: str,
    doc_type: str,
    url: str,
    sha256: str,
) -> dict[str, Any]:
    """Extract one statement or minutes file into the fixed document record."""
    if doc_type not in {"statement", "minutes"}:
        raise ValueError("doc_type must be statement or minutes")
    parsed_date = _event_date(event_date)
    compact = parsed_date.strftime("%Y%m%d")
    source_path = Path(source_path)

    if doc_type == "statement":
        (
            text,
            paragraphs,
            votes_for,
            votes_against,
            dissent_text,
            vote_tally,
        ) = _extract_statement(source_path)
        page_count = None
        blocks_dropped = 0
    else:
        text, paragraphs, page_count, blocks_dropped = _extract_minutes(source_path)
        votes_for, votes_against, dissent_text, vote_tally = [], [], None, None

    return {
        "doc_id": f"{doc_type}-{compact}",
        "event_date": parsed_date,
        "doc_type": doc_type,
        "url": url,
        "sha256": sha256,
        "text": text,
        "paragraphs": paragraphs,
        "n_chars": len(text),
        "page_count": page_count,
        "votes_for": votes_for,
        "votes_against": votes_against,
        "dissent_text": dissent_text,
        "vote_tally": vote_tally,
        "blocks_dropped": int(blocks_dropped),
        "extractor_version": EXTRACTOR_VERSION,
        "extracted_at": datetime.now(timezone.utc),
    }
