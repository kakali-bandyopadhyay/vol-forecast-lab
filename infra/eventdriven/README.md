# Event-driven document registry

This root provisions SNS → SQS → Lambda → DynamoDB ingestion plumbing for document registration events.
The publisher and evidence collector scripts support repeatable phases and sanitized evidence files.
The plan fails until the author's `lambda/doc_registry/handler.py` exists.

Apply `infra/budget` first as the cost guardrail. Keep its state and resources through the evidence run.

## R0 – Confirm identity

```powershell
aws sts get-caller-identity --profile vfl-build --query Arn --output text
```

Confirm it ends in `user/vfl-build`; don't paste it anywhere.

## R1 – Initialize and apply

```powershell
$env:AWS_PROFILE = "vfl-build"
terraform -chdir=infra/eventdriven init
terraform -chdir=infra/eventdriven fmt -check -recursive
terraform -chdir=infra/eventdriven validate
terraform -chdir=infra/eventdriven plan -out tfplan
terraform -chdir=infra/eventdriven apply tfplan
$topic = terraform -chdir=infra/eventdriven output -raw topic_arn
$queue = terraform -chdir=infra/eventdriven output -raw queue_url
$dlqArn = terraform -chdir=infra/eventdriven output -raw dlq_arn
$dlq = terraform -chdir=infra/eventdriven output -raw dlq_url
```

## R2 – Publish all documents

```powershell
python scripts/publish_doc_events.py --phase R2 --topic-arn $topic --profile vfl-build
Start-Sleep -Seconds 60
python scripts/collect_eventdriven_evidence.py --phase R2 --profile vfl-build --queue-url $queue --dlq-url $dlq
```

## R3 – Republish and send a raw duplicate

```powershell
python scripts/publish_doc_events.py --phase R3 --topic-arn $topic --profile vfl-build --republish --raw-duplicate --queue-url $queue
Start-Sleep -Seconds 60
python scripts/collect_eventdriven_evidence.py --phase R3 --profile vfl-build --queue-url $queue --dlq-url $dlq
```

## R4 – Publish a poison event

```powershell
python scripts/publish_doc_events.py --phase R4 --topic-arn $topic --profile vfl-build --poison
Start-Sleep -Seconds 240
python scripts/collect_eventdriven_evidence.py --phase R4 --profile vfl-build --queue-url $queue --dlq-url $dlq
```

## R5 – Accept v2 and redrive

```powershell
terraform -chdir=infra/eventdriven plan -out tfplan -var "accepted_schema_versions=1,2"
terraform -chdir=infra/eventdriven apply tfplan
aws sqs start-message-move-task --source-arn $dlqArn --profile vfl-build
do {
    $move = aws sqs list-message-move-tasks --source-arn $dlqArn --profile vfl-build | ConvertFrom-Json
    $status = $move.Results[0].Status
    if ($status -ne "COMPLETED") { Start-Sleep -Seconds 5 }
} while ($status -ne "COMPLETED")
Start-Sleep -Seconds 60
python scripts/collect_eventdriven_evidence.py --phase R5 --profile vfl-build --queue-url $queue --dlq-url $dlq
```

## R6 – Republish the deterministic poison event

```powershell
python scripts/publish_doc_events.py --phase R6 --topic-arn $topic --profile vfl-build --poison
Start-Sleep -Seconds 60
python scripts/collect_eventdriven_evidence.py --phase R6 --profile vfl-build --queue-url $queue --dlq-url $dlq
```

The poison body is deterministic except for `event_id` and `published_at`.

## R7 – Destroy and verify cleanup

```powershell
New-Item -ItemType Directory -Path results/text -Force | Out-Null
terraform -chdir=infra/eventdriven destroy -no-color 2>&1 | Tee-Object results/text/10d_destroy_raw.txt
# Windows PowerShell 5.1: Tee-Object writes UTF-16; re-save as UTF-8 (no BOM) before sanitising
$raw = Get-Content results/text/10d_destroy_raw.txt -Raw
[System.IO.File]::WriteAllText("$PWD/results/text/10d_destroy_raw.txt", $raw, (New-Object System.Text.UTF8Encoding $false))
python scripts/collect_eventdriven_evidence.py --sanitise-file results/text/10d_destroy_raw.txt --out results/text/10d_destroy.txt
aws dynamodb list-tables --profile vfl-build
```

The deploy user needs `lambda:GetEventSourceMapping` on `*` as well as on the mapping ARN: after the
mapping is deleted, the provider's delete waiter keeps polling it and IAM evaluates that call against
`*` (first destroy on 18 Nov 2026 stopped with AccessDenied; BUILD_GUIDE Issue 31).

## Phase targets (targets, not results)

| Phase | Items in table | Outcomes | DLQ |
|---|---:|---|---:|
| R2 | 266 | written 266 | 0 |
| R3 | 266 | duplicate 267 | 0 |
| R4 | 266 | rejected 3 | 1 |
| R5 | 267 | written +1 | 0 |
| R6 | 267 | duplicate +1 | 0 |
| R7 | – (destroyed) | – | – |
