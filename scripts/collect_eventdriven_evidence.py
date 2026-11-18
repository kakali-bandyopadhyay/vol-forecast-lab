"""Collect sanitized event-driven ingestion evidence from AWS."""

from __future__ import annotations

import argparse
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import re
import sys
import time
from typing import Any, Iterable


DEFAULT_TABLE = "vfl-doc-registry"
DEFAULT_QUEUE = "vfl-doc-registry"
DEFAULT_DLQ = "vfl-doc-registry-dlq"
DEFAULT_FUNCTION = "vfl-doc-registry"
DEFAULT_LOG_GROUP = "/aws/lambda/vfl-doc-registry"
ACCOUNT_ID = re.compile(r"(?<!\d)\d{12}(?!\d)")


def sanitise(text: str) -> str:
    return ACCOUNT_ID.sub("<account>", text)


def _sanitize_tree(value: Any) -> Any:
    if isinstance(value, str):
        return sanitise(value)
    if isinstance(value, dict):
        return {sanitise(str(key)): _sanitize_tree(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_sanitize_tree(item) for item in value]
    return value


def nearest_rank_percentile(values: Iterable[float], percentile: float) -> float | None:
    ordered = sorted(values)
    if not ordered:
        return None
    rank = max(1, int((percentile * len(ordered) + 99.999999) // 100))
    return ordered[rank - 1]


def parse_logs_insights_results(results: list[list[dict[str, str]]]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for row in results:
        fields = {field.get("field"): field.get("value") for field in row}
        outcome = fields.get("outcome")
        count = fields.get("n")
        if outcome is not None and count is not None:
            counts[outcome] = int(count)
    return counts


def _parse_time(value: Any) -> datetime:
    if isinstance(value, datetime):
        parsed = value
    else:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _scan_table(table: Any, *, count_only: bool) -> tuple[int, list[dict[str, Any]]]:
    kwargs: dict[str, Any] = {"Select": "COUNT"} if count_only else {"ProjectionExpression": "published_at, written_at"}
    count = 0
    items: list[dict[str, Any]] = []
    while True:
        page = table.scan(**kwargs)
        count += page.get("Count", 0)
        if not count_only:
            items.extend(page.get("Items", []))
        key = page.get("LastEvaluatedKey")
        if not key:
            break
        kwargs["ExclusiveStartKey"] = key
    return count, items


def _logs_counts(logs: Any, log_group: str, start: datetime, end: datetime, *, sleep: Any = time.sleep) -> dict[str, int]:
    query = logs.start_query(
        logGroupName=log_group,
        startTime=int(start.timestamp()),
        endTime=int(end.timestamp()),
        queryString="filter ispresent(outcome) | stats count(*) as n by outcome",
    )
    query_id = query["queryId"]
    deadline = time.monotonic() + 60
    while True:
        result = logs.get_query_results(queryId=query_id)
        status = result.get("status")
        if status == "Complete":
            return parse_logs_insights_results(result.get("results", []))
        if status in {"Failed", "Cancelled", "Timeout"}:
            raise RuntimeError(f"Logs Insights query ended with status {status}")
        if time.monotonic() >= deadline:
            raise TimeoutError("Logs Insights query did not complete within 60 seconds")
        sleep(2)


def collect_evidence(
    *, phase: str, profile: str = "vfl-build", region: str = "us-east-1",
    table_name: str = DEFAULT_TABLE, queue_url: str, dlq_url: str,
    function_name: str = DEFAULT_FUNCTION, log_group: str = DEFAULT_LOG_GROUP,
    since_minutes: int = 30, session_factory: Any = None, now: datetime | None = None,
    sleep: Any = time.sleep,
) -> dict[str, Any]:
    if session_factory is None:
        import boto3

        session_factory = boto3.Session
    session = session_factory(profile_name=profile, region_name=region)
    dynamodb = session.resource("dynamodb").Table(table_name)
    sqs = session.client("sqs")
    logs = session.client("logs")
    cloudwatch = session.client("cloudwatch")
    end = now or datetime.now(timezone.utc)
    if end.tzinfo is None:
        end = end.replace(tzinfo=timezone.utc)
    start = end - timedelta(minutes=since_minutes)

    item_count, _ = _scan_table(dynamodb, count_only=True)
    if "://" not in queue_url:
        queue_url = sqs.get_queue_url(QueueName=queue_url)["QueueUrl"]
    if "://" not in dlq_url:
        dlq_url = sqs.get_queue_url(QueueName=dlq_url)["QueueUrl"]
    main_attrs = sqs.get_queue_attributes(
        QueueUrl=queue_url,
        AttributeNames=["ApproximateNumberOfMessages", "ApproximateNumberOfMessagesNotVisible"],
    ).get("Attributes", {})
    dlq_attrs = sqs.get_queue_attributes(
        QueueUrl=dlq_url, AttributeNames=["ApproximateNumberOfMessages"]
    ).get("Attributes", {})
    outcome_counts = _logs_counts(logs, log_group, start, end, sleep=sleep)
    metrics: dict[str, int] = {}
    for metric_name in ("Invocations", "Errors"):
        response = cloudwatch.get_metric_statistics(
            Namespace="AWS/Lambda",
            MetricName=metric_name,
            Dimensions=[{"Name": "FunctionName", "Value": function_name}],
            StartTime=start,
            EndTime=end,
            Period=60,
            Statistics=["Sum"],
        )
        metrics[metric_name.lower()] = int(sum(point.get("Sum", 0) for point in response.get("Datapoints", [])))
    _, items = _scan_table(dynamodb, count_only=False)
    latencies = []
    for item in items:
        if item.get("published_at") and item.get("written_at"):
            latencies.append((_parse_time(item["written_at"]) - _parse_time(item["published_at"])).total_seconds() * 1000)

    evidence = {
        "phase": phase,
        "generated_at": end.astimezone(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z"),
        "window_minutes": since_minutes,
        "item_count": item_count,
        "main_queue_approximate_number_of_messages": int(main_attrs.get("ApproximateNumberOfMessages", 0)),
        "main_queue_approximate_number_of_messages_not_visible": int(main_attrs.get("ApproximateNumberOfMessagesNotVisible", 0)),
        "dlq_approximate_number_of_messages": int(dlq_attrs.get("ApproximateNumberOfMessages", 0)),
        "outcome_counts": outcome_counts,
        "lambda_invocations": metrics["invocations"],
        "lambda_errors": metrics["errors"],
        "end_to_end_latency_p50_ms": nearest_rank_percentile(latencies, 50),
        "end_to_end_latency_p95_ms": nearest_rank_percentile(latencies, 95),
    }
    return _sanitize_tree(evidence)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--phase")
    parser.add_argument("--since-minutes", type=int, default=30)
    parser.add_argument("--profile", default="vfl-build")
    parser.add_argument("--region", default="us-east-1")
    parser.add_argument("--table", default=DEFAULT_TABLE)
    parser.add_argument("--queue-url", default=DEFAULT_QUEUE)
    parser.add_argument("--dlq-url", default=DEFAULT_DLQ)
    parser.add_argument("--function-name", default=DEFAULT_FUNCTION)
    parser.add_argument("--log-group", default=DEFAULT_LOG_GROUP)
    parser.add_argument("--sanitise-file", type=Path)
    parser.add_argument("--out", type=Path)
    return parser


def main(argv: list[str] | None = None, *, session_factory: Any = None) -> int:
    args = _parser().parse_args(argv)
    if args.sanitise_file:
        if not args.out:
            raise SystemExit("--out is required with --sanitise-file")
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(sanitise(args.sanitise_file.read_text(encoding="utf-8")), encoding="utf-8")
        return 0
    if not args.phase:
        raise SystemExit("--phase is required unless using --sanitise-file")
    evidence = collect_evidence(
        phase=args.phase,
        profile=args.profile,
        region=args.region,
        table_name=args.table,
        queue_url=args.queue_url,
        dlq_url=args.dlq_url,
        function_name=args.function_name,
        log_group=args.log_group,
        since_minutes=args.since_minutes,
        session_factory=session_factory,
    )
    output_dir = Path("results/eventdriven")
    output_dir.mkdir(parents=True, exist_ok=True)
    output = output_dir / f"evidence_{args.phase}.json"
    output.write_text(json.dumps(_sanitize_tree(evidence), indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(f"evidence={output.as_posix()}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
