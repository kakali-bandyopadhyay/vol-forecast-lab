"""Run one Step 10c Kafka role from the command line (plumbing; logic lives in volab.text.consumers).

  python scripts/run_kafka_consumers.py uploader  --max-messages 266
  python scripts/run_kafka_consumers.py publisher
  python scripts/run_kafka_consumers.py indexer   --max-messages 266

Bucket from --bucket or VFL_LAKE_BUCKET; AWS profile from --profile (default vfl-build).
Counters are merged into results/text/10c_kafka.json.
"""

from __future__ import annotations

import argparse
import functools
import json

from volab.text import consumers


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("role", choices=("uploader", "publisher", "indexer"))
    parser.add_argument("--bucket", default=None)
    parser.add_argument("--profile", default="vfl-build")
    parser.add_argument("--region", default="us-east-1")
    parser.add_argument("--max-messages", type=int, default=None)
    args = parser.parse_args(argv)

    import boto3

    s3 = boto3.Session(profile_name=args.profile, region_name=args.region).client("s3")
    bucket = consumers._bucket(args.bucket)

    if args.role == "publisher":
        counts = consumers.clean_publisher(s3=s3, bucket=bucket)
        consumers.write_counters("clean_publisher", counts)
    elif args.role == "uploader":
        handler = functools.partial(consumers.uploader, s3=s3, bucket=bucket)
        counts = consumers.run_consumer("docs.raw", "vfl-uploader", handler,
                                        max_messages=args.max_messages, counters_name="uploader")
    else:
        handler = functools.partial(consumers.indexer, s3=s3, bucket=bucket)
        counts = consumers.run_consumer("docs.clean", "vfl-indexer", handler,
                                        max_messages=args.max_messages, counters_name="indexer")
    print(json.dumps({args.role: counts}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
