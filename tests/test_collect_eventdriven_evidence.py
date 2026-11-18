"""AI-written plumbing test – reviewed line by line by the author."""

from datetime import datetime, timezone
import json

import scripts.collect_eventdriven_evidence as collector


def test_sanitise_account_ids_and_preserve_other_numbers():
    account_id = "123456" + "789012"
    source = f"arn:aws:iam::{account_id}:role/x plain {account_id} 266 2026-11-18T01:02:03Z"
    result = collector.sanitise(source)
    assert "123456789012" not in result
    assert result.count("<account>") == 2
    assert "266" in result and "2026-11-18T01:02:03Z" in result


def test_nearest_rank_percentiles():
    values = [10, 20, 30, 40, 50]
    assert collector.nearest_rank_percentile(values, 50) == 30
    assert collector.nearest_rank_percentile(values, 95) == 50
    assert collector.nearest_rank_percentile([], 50) is None


def test_logs_insights_rows_parse_to_outcome_counts():
    rows = [
        [{"field": "outcome", "value": "written"}, {"field": "n", "value": "266"}],
        [{"field": "outcome", "value": "retry"}, {"field": "n", "value": "3"}],
    ]
    assert collector.parse_logs_insights_results(rows) == {"written": 266, "retry": 3}


class FakeTable:
    def __init__(self):
        self.calls = 0

    def scan(self, **kwargs):
        self.calls += 1
        if kwargs.get("Select") == "COUNT":
            return {"Count": 1, "LastEvaluatedKey": {"pk": "x"}} if "ExclusiveStartKey" not in kwargs else {"Count": 2}
        if "ExclusiveStartKey" not in kwargs:
            return {"Items": [{"published_at": "2026-11-18T00:00:00Z", "written_at": "2026-11-18T00:00:01Z"}], "LastEvaluatedKey": {"pk": "x"}}
        return {"Items": [{"published_at": "2026-11-18T00:00:00Z", "written_at": "2026-11-18T00:00:03Z"}]}


class FakeDynamo:
    def __init__(self):
        self.table = FakeTable()

    def Table(self, _name):
        return self.table


class FakeLogs:
    def start_query(self, **_kwargs):
        return {"queryId": "q"}

    def get_query_results(self, **_kwargs):
        return {"status": "Complete", "results": [[{"field": "outcome", "value": "written"}, {"field": "n", "value": "2"}]]}


class FakeSQS:
    def get_queue_url(self, **kwargs):
        return {"QueueUrl": f"https://sqs.example/{kwargs['QueueName']}"}

    def get_queue_attributes(self, **_kwargs):
        return {"Attributes": {"ApproximateNumberOfMessages": "1", "ApproximateNumberOfMessagesNotVisible": "2"}}


class FakeCloudWatch:
    def get_metric_statistics(self, **kwargs):
        return {"Datapoints": [{"Sum": 5 if kwargs["MetricName"] == "Invocations" else 1}]}


class FakeSession:
    def __init__(self):
        self.dynamo = FakeDynamo()

    def resource(self, name):
        assert name == "dynamodb"
        return self.dynamo

    def client(self, name):
        return {"sqs": FakeSQS(), "logs": FakeLogs(), "cloudwatch": FakeCloudWatch()}[name]


def test_evidence_file_has_required_values_and_is_written(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    now = datetime(2026, 11, 18, 1, 0, tzinfo=timezone.utc)
    real_collect = collector.collect_evidence
    monkeypatch.setattr(
        collector, "collect_evidence",
        lambda **kwargs: real_collect(**kwargs, now=now, sleep=lambda _seconds: None),
    )
    assert collector.main(
        ["--phase", "R2", "--queue-url", "queue", "--dlq-url", "dlq"],
        session_factory=lambda **_: FakeSession(),
    ) == 0
    output = tmp_path / "results/eventdriven/evidence_R2.json"
    stored = json.loads(output.read_text(encoding="utf-8"))
    assert set(stored) == {
        "phase", "generated_at", "window_minutes", "item_count",
        "main_queue_approximate_number_of_messages",
        "main_queue_approximate_number_of_messages_not_visible",
        "dlq_approximate_number_of_messages", "outcome_counts", "lambda_invocations",
        "lambda_errors", "end_to_end_latency_p50_ms", "end_to_end_latency_p95_ms",
    }
    assert stored["item_count"] == 3
    assert stored["end_to_end_latency_p50_ms"] == 1000
