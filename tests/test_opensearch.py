"""Contract tests for the author-owned Step 10c OpenSearch adapter."""

from __future__ import annotations

import urllib.error
import urllib.request

import pandas as pd
import pytest
from opensearchpy import OpenSearch

from volab.text import opensearch_index as os_index


EXPECTED_PROPERTIES = {
    "chunk_id": {"type": "keyword"},
    "doc_id": {"type": "keyword"},
    "doc_type": {"type": "keyword"},
    "event_date": {"type": "date"},
    "page": {"type": "integer"},
    "header": {"type": "text", "analyzer": "english"},
    "text": {"type": "text", "analyzer": "english"},
    "content_hash": {"type": "keyword"},
}


def test_mapping_matches_the_contract_field_by_field():
    assert os_index.INDEX == "fomc_chunks_v1"
    assert os_index.MAPPING["mappings"]["properties"] == EXPECTED_PROPERTIES


def test_bm25_query_has_boost_and_only_requested_inclusive_filters():
    body = os_index.bm25_query(
        "target range", date_from="2010-01-01", date_to="2010-12-31", doc_type="statement"
    )
    query = body["query"]["bool"]
    assert query["must"][0]["multi_match"] == {
        "query": "target range", "fields": ["header^2", "text"]
    }
    assert query["filter"] == [
        {"range": {"event_date": {"gte": "2010-01-01", "lte": "2010-12-31"}}},
        {"term": {"doc_type": "statement"}},
    ]

    unfiltered = os_index.bm25_query("target range")
    assert "filter" not in unfiltered["query"]["bool"]
    lower_only = os_index.bm25_query("target range", date_from="2010-01-01")
    assert lower_only["query"]["bool"]["filter"] == [
        {"range": {"event_date": {"gte": "2010-01-01"}}}
    ]


def test_rrf_scores_for_two_ranked_lists_are_hand_computed():
    # With k=60, A=[c1,c2,c3], B=[c3,c1,c4]: c1=1/61+1/62=.032522,
    # c3=1/63+1/61=.032266, c2=1/62=.016129, c4=1/63=.015873.
    fused = os_index.rrf([["c1", "c2", "c3"], ["c3", "c1", "c4"]], k=60)
    assert fused == [
        ("c1", 1 / 61 + 1 / 62),
        ("c3", 1 / 63 + 1 / 61),
        ("c2", 1 / 62),
        ("c4", 1 / 63),
    ]


class FakeBulkClient:
    def __init__(self):
        self.docs = {}
        self.created_indices = set()
        self.indices = self.FakeIndices(self)

    class FakeIndices:
        def __init__(self, client):
            self.client = client

        def exists(self, index):
            return index in self.client.created_indices

        def create(self, index, body):
            self.client.created_indices.add(index)
            return {"acknowledged": True}

    def bulk(self, body, refresh=False):
        items = []
        for i in range(0, len(body), 2):
            action = body[i]["index"]
            created = action["_id"] not in self.docs
            self.docs[action["_id"]] = body[i + 1]
            items.append({"index": {
                "_id": action["_id"], "status": 201 if created else 200,
                "result": "created" if created else "updated",
            }})
        return {"errors": False, "items": items}


def test_index_chunks_is_idempotent_for_an_identical_second_call():
    client = FakeBulkClient()
    chunks = pd.DataFrame([{
        "chunk_id": "statement-20200129-0001", "doc_id": "statement-20200129",
        "doc_type": "statement", "event_date": "2020-01-29", "page": 1,
        "text": "The Committee maintained the target range.", "content_hash": "abc123",
    }])
    first = os_index.index_chunks_os(client, chunks)
    second = os_index.index_chunks_os(client, chunks)
    assert first["created"] == 1
    assert second["created"] == 0
    assert len(client.docs) == 1
    assert "header" in next(iter(client.docs.values()))


def _docker_endpoint_available() -> bool:
    try:
        with urllib.request.urlopen("http://127.0.0.1:9200", timeout=0.5):
            return True
    except (OSError, urllib.error.URLError):
        return False


@pytest.mark.docker
@pytest.mark.skipif(not _docker_endpoint_available(), reason="OpenSearch is not answering on localhost:9200")
def test_docker_mapping_and_filtered_search():
    client = OpenSearch(hosts=[{"host": "127.0.0.1", "port": 9200}], use_ssl=False)
    os_index.ensure_index(client)
    mapping = client.indices.get_mapping(index=os_index.INDEX)
    assert mapping[os_index.INDEX]["mappings"] == os_index.MAPPING["mappings"]

    docs = pd.DataFrame([
        {"chunk_id": "old", "doc_id": "statement-19990101", "doc_type": "statement",
         "event_date": "1999-01-01", "page": 1, "text": "target range", "content_hash": "old"},
        {"chunk_id": "in", "doc_id": "statement-20000101", "doc_type": "statement",
         "event_date": "2000-01-01", "page": 1, "text": "target range", "content_hash": "in"},
        {"chunk_id": "new", "doc_id": "minutes-20010101", "doc_type": "minutes",
         "event_date": "2001-01-01", "page": 1, "text": "target range", "content_hash": "new"},
    ])
    os_index.index_chunks_os(client, docs)
    found = os_index.search_bm25(client, "target range", date_from="2000-01-01", date_to="2000-12-31")
    assert found
    assert set(found).issubset({"in"})
