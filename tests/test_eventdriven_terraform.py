"""AI-written plumbing test – reviewed line by line by the author."""

from pathlib import Path
import re


ROOT = Path(__file__).resolve().parents[1]
TF_DIR = ROOT / "infra" / "eventdriven"
MAIN = (TF_DIR / "main.tf").read_text(encoding="utf-8")


def test_event_source_and_retry_settings():
    assert re.search(r"maxReceiveCount\s*=\s*3", MAIN)
    assert re.search(r"visibility_timeout_seconds\s*=\s*60", MAIN)
    assert re.search(r"timeout\s*=\s*10", MAIN)
    assert "function_response_types            = [\"ReportBatchItemFailures\"]" in MAIN
    assert "raw_message_delivery = true" in MAIN
    assert "maximum_concurrency = 2" in MAIN
    assert 'handler          = "handler.lambda_handler"' in MAIN


def test_role_policy_has_exact_scoped_action_set():
    policy = MAIN.split('resource "aws_iam_role_policy" "registry" {', 1)[1].split('\nresource ', 1)[0]
    actions = set(re.findall(r'"((?:sqs|dynamodb|logs):[^"*]+)"', policy))
    expected = {
        "sqs:ReceiveMessage", "sqs:DeleteMessage", "sqs:GetQueueAttributes", "sqs:ChangeMessageVisibility",
        "dynamodb:PutItem", "logs:CreateLogStream", "logs:PutLogEvents",
    }
    assert actions == expected
    assert not re.search(r'Action\s*=\s*"\*"|Resource\s*=\s*"\*"', policy)
    assert "aws_sqs_queue.dlq.arn" not in policy


def test_queue_policy_and_archive_and_no_account_ids():
    queue_policy = MAIN.split('resource "aws_sqs_queue_policy" "main" {', 1)[1].split('\nresource ', 1)[0]
    assert '"aws:SourceArn"' in queue_policy
    assert 'raw_message_delivery = true' in MAIN
    assert "data \"archive_file\" \"registry\"" in MAIN
    for path in TF_DIR.glob("*.tf"):
        assert not re.search(r"\b\d{12}\b", path.read_text(encoding="utf-8"))
