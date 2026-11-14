"""Offline Step 10b tests: fixtures, chunking, schemas, indexing and metrics."""

from __future__ import annotations

from datetime import date, datetime, timezone
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq
import pymupdf
import pytest
from qdrant_client import QdrantClient

from volab.text import chunk, dq, extract, index, pipeline
from volab.text.extract import ExtractionError, extract_document

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "fomc"

LONG_PROSE = (
    "The Committee discussed financial market developments at length, including credit "
    "conditions, market functioning, and the outlook for policy over the coming months "
    "and quarters, with attention to the risks on both sides of its mandate."
)


def _extract(name: str, doc_type: str, event_date: str):
    return extract_document(
        FIXTURES / name,
        event_date=event_date,
        doc_type=doc_type,
        url=f"https://example.test/{name}",
        sha256="fixture",
    )


# --- extraction ------------------------------------------------------------


def test_statement_fixtures_extract_body_and_votes():
    expected = {
        "20200129": (10, 0),
        "20190731": (8, 2),
        "20100127": (9, 1),
    }
    for event_date, (n_for, n_against) in expected.items():
        document = _extract(f"statement-{event_date}.html", "statement", event_date)
        assert document["text"].startswith("Information received since the Federal Open Market Committee met in")
        assert "Implementation Note" not in document["text"]
        assert "media inquiries" not in document["text"]
        assert "Last Update:" not in document["text"]
        assert len(document["votes_for"]) == n_for
        assert len(document["votes_against"]) == n_against
        assert document["page_count"] is None

    assert _extract("statement-20190731.html", "statement", "20190731")["dissent_text"].startswith("who preferred")
    assert _extract("statement-20100127.html", "statement", "20100127")["votes_against"] == ["Thomas M. Hoenig"]


def test_two_column_minutes_keep_left_column_before_right():
    text = _extract("minutes-20200129.pdf", "minutes", "20200129")["text"]

    assert text.index("Patrick Harker, President") < text.index(
        "By unanimous vote, the Committee selected Lorie"
    )


def test_join_raw_joins_a_split_paragraph_without_a_space_after_hyphen():
    paragraphs = extract._join_raw([
        (1, "The report discusses para-"),
        (1, "graphs 1 and 2 in detail."),
    ])

    assert len(paragraphs) == 1
    assert paragraphs[0]["page"] == 1
    assert "paragraphs 1 and 2" in paragraphs[0]["text"]


def test_vote_record_handles_separate_and_mangled_against_paragraphs():
    supporting = (
        "Voting for the monetary policy action were Jerome H. Powell, Chair; "
        "John C. Williams, Vice Chair."
    )

    _, against, dissent = extract._vote_record([
        supporting,
        "Voting against the monetary policy action were Thomas M. Hoenig, "
        "who believed that policy was too easy.",
    ])
    assert against == ["Thomas M. Hoenig"]
    assert dissent.startswith("who believed")

    _, against, dissent = extract._vote_record([
        supporting,
        "Voting against the policy action were Charles I. Plosser. "
        "President Fisher believed that policy was too easy.",
    ])
    assert against == ["Charles I. Plosser"]
    assert dissent.startswith("President Fisher")

    _, against, dissent = extract._vote_record([
        supporting,
        "Voting against the policy action were Esther L. George, "
        "each of whom preferred a higher rate.",
    ])
    assert against == ["Esther L. George"]
    assert dissent.startswith("each of whom")

    votes_for, _, _ = extract._vote_record([
        "Voting for the monetary policy action were Jerome H. Powell, Chair, "
        "John C. Williams, Vice Chair; Michelle W. Bowman; Lael Brainard; "
        "and Randal K. Quarles. Ms. Daly voted as an alternate member at this meeting."
    ])
    assert votes_for == [
        "Jerome H. Powell", "John C. Williams", "Michelle W. Bowman",
        "Lael Brainard", "Randal K. Quarles",
    ]


def test_2026_statement_uses_a_vote_tally():
    document = _extract("statement-20260729.html", "statement", "20260729")

    assert document["votes_for"] == []
    assert len(document["votes_against"]) == 3
    assert document["vote_tally"] == "9-3"


def test_dq_uses_vote_tally_and_checks_the_dissent_count(tmp_path: Path):
    documents = pd.DataFrame([
        {"doc_id": "statement-20260729", "event_date": date(2026, 7, 29),
         "doc_type": "statement", "url": "u", "sha256": "s", "text": "x" * 600,
         "n_chars": 600, "page_count": None, "votes_for": [], "votes_against": ["A", "B"],
         "dissent_text": "who", "vote_tally": "9-3", "extractor_version": "1.0",
         "extracted_at": datetime(2026, 11, 15, tzinfo=timezone.utc)},
        {"doc_id": "statement-20260916", "event_date": date(2026, 9, 16),
         "doc_type": "statement", "url": "u2", "sha256": "s2", "text": "x" * 600,
         "n_chars": 600, "page_count": None, "votes_for": [], "votes_against": ["A", "B", "C"],
         "dissent_text": "who", "vote_tally": "9-3", "extractor_version": "1.0",
         "extracted_at": datetime(2026, 11, 15, tzinfo=timezone.utc)},
    ])
    documents_path = tmp_path / "documents.parquet"
    documents.to_parquet(documents_path, index=False)
    events = tmp_path / "fomc.csv"
    events.write_text("event_date\n2026-07-29\n2026-09-16\n", encoding="utf-8")
    manifest = tmp_path / "manifest.json"
    manifest.write_text(
        '{"files":{"statement/20260729.html":{"status":"ok","sha256":"s"},'
        '"statement/20260916.html":{"status":"ok","sha256":"s2"}}}',
        encoding="utf-8",
    )

    flags = dq.check_documents(
        documents_path,
        manifest_path=manifest,
        events_file=events,
        chunks_path=None,
        now=datetime(2026, 11, 15, tzinfo=timezone.utc),
    )

    rules = [flag["rule"] for flag in flags]
    assert "votes_for_empty" not in rules
    assert rules.count("vote_count_mismatch") == 1


def test_dq_treats_parquet_nan_vote_tally_as_missing(tmp_path: Path):
    documents = _documents_frame()
    documents["votes_for"] = pd.Series([[]], dtype=object)
    documents["vote_tally"] = None
    documents_path = tmp_path / "documents.parquet"
    pipeline._write_table(documents, documents_path, pipeline.DOCUMENT_SCHEMA)
    events = tmp_path / "fomc.csv"
    events.write_text("event_date\n2020-01-29\n", encoding="utf-8")
    manifest = tmp_path / "manifest.json"
    manifest.write_text(
        '{"files":{"statement/20200129.html":{"status":"ok","sha256":"s"}}}',
        encoding="utf-8",
    )

    flags = dq.check_documents(
        documents_path,
        manifest_path=manifest,
        events_file=events,
        chunks_path=None,
        now=datetime(2026, 11, 15, tzinfo=timezone.utc),
    )

    rules = [flag["rule"] for flag in flags]
    assert "vote_count_mismatch" not in rules
    assert "votes_for_empty" in rules


def test_noise_block_drops_tables_but_keeps_prose():
    assert extract._noise_block("Number of participants")
    assert extract._noise_block("1.2 3.4 5.6 7.8 9.0")
    assert not extract._noise_block(
        "The Committee discussed the outlook for economic activity and inflation."
    )
    assert extract._header_line("Minutes of the Federal Open Market Committee 12")


def _synthetic_minutes(tmp_path: Path, first: str, second: str):
    path = tmp_path / "synthetic.pdf"
    document = pymupdf.open()
    page = document.new_page()
    page.insert_text((72, 72), first, fontsize=12)
    page.insert_textbox(pymupdf.Rect(72, 100, 540, 300), second, fontsize=10)
    document.save(path)
    document.close()
    return extract_document(
        path, event_date="20200129", doc_type="minutes", url="u", sha256="s"
    )


def test_heading_before_prose_is_kept_and_chart_title_before_numbers_is_dropped(tmp_path: Path):
    heading = "Developments in Financial Markets and Open Market Operations"
    kept = _synthetic_minutes(tmp_path, heading, LONG_PROSE)
    assert heading in kept["text"]

    title = "Uncertainty about core PCE inflation"
    numbers = "1.2 3.4 5.6 7.8 9.0 10.1 11.2 12.3 13.4 14.5"
    dropped = _synthetic_minutes(tmp_path, title, numbers)
    assert title not in dropped["text"]


@pytest.mark.parametrize(
    "label",
    [
        "Longer run",
        "Number of participants",
        "Percent",
        "Lower Broadly Higher similar",
    ],
)
def test_chart_label_before_a_note_is_dropped(tmp_path: Path, label: str):
    document = _synthetic_minutes(
        tmp_path,
        label,
        "NOTE: In the upper panel, each dot is one participant's projection.",
    )

    assert label not in document["text"]


def test_single_word_before_body_is_dropped(tmp_path: Path):
    document = _synthetic_minutes(tmp_path, "Attendance", LONG_PROSE)

    assert "Attendance" not in document["text"]


def test_forecast_uncertainty_heading_before_body_is_kept(tmp_path: Path):
    document = _synthetic_minutes(tmp_path, "Forecast Uncertainty", LONG_PROSE)

    assert "Forecast Uncertainty" in document["text"]


def test_minutes_fixture_drops_headers_and_keeps_page_map():
    document = _extract("minutes-20200129.pdf", "minutes", "20200129")

    assert document["page_count"] == 3
    assert document["paragraphs"][0]["page"] == 1
    assert document["paragraphs"][-1]["page"] == 3
    assert "Page 1" not in document["text"]
    assert not any(paragraph["text"].strip().isdigit() for paragraph in document["paragraphs"])
    assert "The elected members and alternate members were as follows:" in document["text"]
    assert document["text"].startswith("A joint meeting")
    assert "Lorie K. Logan, Manager" not in document["text"]
    assert "Ann E. Misback" not in document["text"]
    assert "Annual Organizational Matters" in document["text"]


def test_missing_article_raises_extraction_error(tmp_path: Path):
    source = tmp_path / "bad.html"
    source.write_text("<html><body><p>No article here.</p></body></html>", encoding="utf-8")

    with pytest.raises(ExtractionError, match="article"):
        extract_document(source, event_date="20200129", doc_type="statement", url="u", sha256="s")


# --- chunking --------------------------------------------------------------


def test_chunks_are_bounded_and_contiguous():
    for name, doc_type, event_date in (
        ("statement-20200129.html", "statement", "20200129"),
        ("minutes-20200129.pdf", "minutes", "20200129"),
    ):
        document = _extract(name, doc_type, event_date)
        chunks = chunk.chunk_document(document)

        assert chunks
        assert max(item["n_tokens"] for item in chunks) <= 300
        assert [item["ord"] for item in chunks] == list(range(len(chunks)))
        assert len({item["chunk_id"] for item in chunks}) == len(chunks)
        joined = " ".join(item["text"] for item in chunks)
        assert " ".join(joined.split()) == " ".join(document["text"].split())


# --- parquet schemas -------------------------------------------------------


def _documents_frame() -> pd.DataFrame:
    return pd.DataFrame([{
        "doc_id": "statement-20200129",
        "event_date": date(2020, 1, 29),
        "doc_type": "statement",
        "url": "u",
        "sha256": "s",
        "text": "x" * 600,
        "n_chars": 600,
        "page_count": None,
        "votes_for": ["A", "B"],
        "votes_against": ["C"],
        "dissent_text": "who preferred",
        "vote_tally": None,
        "blocks_dropped": 0,
        "extractor_version": "1.0",
        "extracted_at": datetime(2026, 11, 15, tzinfo=timezone.utc),
    }])


def _chunks_frame() -> pd.DataFrame:
    return pd.DataFrame([{
        "chunk_id": "statement-20200129-0000",
        "doc_id": "statement-20200129",
        "doc_type": "statement",
        "event_date": date(2020, 1, 29),
        "ord": 0,
        "page": None,
        "text": "body",
        "n_tokens": 1,
        "content_hash": "h",
    }])


def test_fixed_parquet_schemas_and_vote_lists_round_trip(tmp_path: Path):
    documents_path = tmp_path / "documents.parquet"
    chunks_path = tmp_path / "chunks.parquet"
    pipeline._write_table(_documents_frame(), documents_path, pipeline.DOCUMENT_SCHEMA)
    pipeline._write_table(_chunks_frame(), chunks_path, pipeline.CHUNK_SCHEMA)

    assert pq.read_schema(documents_path).equals(pipeline.DOCUMENT_SCHEMA, check_metadata=False)
    assert pq.read_schema(chunks_path).equals(pipeline.CHUNK_SCHEMA, check_metadata=False)
    round_tripped = pq.read_table(documents_path).to_pandas()
    assert list(round_tripped.loc[0, "votes_for"]) == ["A", "B"]
    assert list(round_tripped.loc[0, "votes_against"]) == ["C"]


# --- qdrant indexing -------------------------------------------------------


def _fake_embedder(texts):
    return [
        [float((sum(text.encode("utf-8")) + offset) % 11) for offset in range(8)]
        for text in texts
    ]


def _index_chunks(tmp_path: Path, chunks: pd.DataFrame, documents: pd.DataFrame, client=None):
    client = client or QdrantClient(":memory:")
    counts = index.index_chunks(
        chunks,
        documents,
        client=client,
        embedder=_fake_embedder,
        cache_path=tmp_path / "embeddings.parquet",
        dense_dim=8,
    )
    return client, counts


def _three_chunks() -> tuple[pd.DataFrame, pd.DataFrame]:
    chunks = pd.DataFrame([
        {"chunk_id": "statement-20200129-0000", "doc_id": "statement-20200129",
         "doc_type": "statement", "event_date": pd.Timestamp("2020-01-29"), "page": None,
         "text": "the committee maintains the target range", "content_hash": "h1"},
        {"chunk_id": "statement-20200129-0001", "doc_id": "statement-20200129",
         "doc_type": "statement", "event_date": pd.Timestamp("2020-01-29"), "page": None,
         "text": "voting for the monetary policy action", "content_hash": "h2"},
        {"chunk_id": "minutes-20200129-0000", "doc_id": "minutes-20200129",
         "doc_type": "minutes", "event_date": pd.Timestamp("2020-01-29"), "page": 1,
         "text": "participants discussed the economic outlook", "content_hash": "h3"},
    ])
    documents = pd.DataFrame([
        {"doc_id": "statement-20200129", "url": "u1"},
        {"doc_id": "minutes-20200129", "url": "u2"},
    ])
    return chunks, documents


def test_index_is_idempotent_and_tracks_changes(tmp_path: Path):
    chunks, documents = _three_chunks()
    client = QdrantClient(":memory:")
    _, first = _index_chunks(tmp_path, chunks, documents, client=client)
    _, second = _index_chunks(tmp_path, chunks, documents, client=client)
    assert first == {"upserted": 3, "unchanged": 0, "deleted": 0, "total": 3, "tokens": 0}
    assert second == {"upserted": 0, "unchanged": 3, "deleted": 0, "total": 3, "tokens": 0}

    changed = chunks.copy()
    changed.loc[0, "text"] = "the committee changed the target range"
    changed.loc[0, "content_hash"] = "h1b"
    _, third = _index_chunks(tmp_path, changed, documents, client=client)
    assert third["upserted"] == 1 and third["unchanged"] == 2

    _, fourth = _index_chunks(tmp_path, changed.iloc[:2], documents, client=client)
    assert fourth["deleted"] == 1


def test_embed_texts_batches_at_256_and_saves_the_cache(tmp_path: Path):
    calls = []

    def embedder(texts):
        calls.append(len(texts))
        return [[0.0] * 8 for _ in texts]

    items = [(f"h{i}", f"text {i}") for i in range(300)]
    known, tokens = index._embed_texts(
        items, embedder=embedder, cache_path=tmp_path / "embeddings.parquet"
    )

    assert calls == [256, 44]
    assert len(known) == 300
    assert tokens == 0
    assert (tmp_path / "embeddings.parquet").is_file()


def test_date_filters_apply_in_every_search_mode(tmp_path: Path):
    chunks = pd.DataFrame([
        {"chunk_id": f"statement-{date}-0000", "doc_id": f"statement-{date}",
         "doc_type": "statement", "event_date": pd.Timestamp(date), "page": None,
         "text": f"target {date}", "content_hash": f"h{date}"}
        for date in ("2019-12-31", "2020-06-30", "2021-12-31")
    ])
    documents = pd.DataFrame([
        {"doc_id": f"statement-{date}", "url": f"u{date}"}
        for date in ("2019-12-31", "2020-06-30", "2021-12-31")
    ])
    client, _ = _index_chunks(tmp_path, chunks, documents)

    for mode in ("hybrid", "dense", "sparse"):
        results = index.search(
            "target",
            date_from="2020-01-01",
            date_to="2020-12-31",
            k=5,
            mode=mode,
            client=client,
            embedder=_fake_embedder,
        )
        assert results
        assert {result["event_date"] for result in results} == {"2020-06-30"}


# --- eval metrics and the index guard --------------------------------------


def test_retrieval_metrics_on_a_hand_computed_toy():
    metrics = pipeline._metrics(["a", "b"], ["x", "a", "b"])
    assert metrics["recall_at_5"] == 1.0
    assert metrics["hit_at_5"] == 1.0
    assert metrics["mrr_at_10"] == 0.5
    assert metrics["rank"] == 2

    miss = pipeline._metrics(["a"], ["x", "y"])
    assert miss == {"recall_at_5": 0.0, "hit_at_5": 0.0, "mrr_at_10": 0.0, "rank": 0}


def test_index_guard_rejects_untracked_or_dirty_eval_file(monkeypatch):
    monkeypatch.setattr(
        pipeline, "_git", lambda args: SimpleNamespace(returncode=1, stdout="")
    )
    with pytest.raises(SystemExit, match="not committed"):
        pipeline.require_committed("data/text/eval_questions.csv")

    monkeypatch.setattr(
        pipeline, "_git", lambda args: SimpleNamespace(returncode=0, stdout=" M file")
    )
    with pytest.raises(SystemExit, match="uncommitted"):
        pipeline.require_committed("data/text/eval_questions.csv")

    monkeypatch.setattr(
        pipeline, "_git", lambda args: SimpleNamespace(returncode=0, stdout="")
    )
    pipeline.require_committed("data/text/eval_questions.csv")
