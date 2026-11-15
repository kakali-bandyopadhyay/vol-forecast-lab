"""Offline checks for the Step 10c D1/D2 plumbing (Claude plumbing; reviewed by the author)."""

import re
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
MAIN_TF = (REPO / "infra" / "terraform" / "main.tf").read_text(encoding="utf-8")
DOCKERFILE = (REPO / "lambda" / "extract" / "Dockerfile").read_text(encoding="utf-8")


def _block(kind: str, name: str) -> str:
    start = MAIN_TF.index(f'resource "{kind}" "{name}" {{')
    depth, i = 0, MAIN_TF.index("{", start)
    for j in range(i, len(MAIN_TF)):
        depth += {"{": 1, "}": -1}.get(MAIN_TF[j], 0)
        if depth == 0:
            return MAIN_TF[start:j + 1]
    raise AssertionError("unbalanced block")


def test_lambda_is_an_image_created_only_when_a_uri_is_given():
    """D2: image package, no zip/layer; Lambda, permission and trigger wait for extract_image_uri."""
    block = _block("aws_lambda_function", "vfl_extract")
    assert 'package_type  = "Image"' in block and "var.extract_image_uri" in block
    assert 'count         = var.extract_image_uri == "" ? 0 : 1' in block
    assert "archive_file" not in MAIN_TF and "layers" not in block and "handler " not in block
    for kind, name in (("aws_lambda_permission", "vfl_s3_invoke"), ("aws_s3_bucket_notification", "vfl_raw_objects")):
        assert "count" in _block(kind, name)


def test_ecr_repository_is_immutable_scanned_and_pull_limited_to_the_function():
    """D2: ECR repo vfl-extract, scan on push, keep 3 images, Lambda-only pull policy."""
    repo = _block("aws_ecr_repository", "vfl_extract")
    assert 'name                 = "vfl-extract"' in repo and "scan_on_push = true" in repo
    assert '"IMMUTABLE"' in repo
    assert "countNumber = 3" in _block("aws_ecr_lifecycle_policy", "vfl_extract")
    policy = _block("aws_ecr_repository_policy", "vfl_extract")
    assert "lambda.amazonaws.com" in policy and "function:vfl-extract" in policy


def test_glue_table_has_the_d1_columns():
    """D1: vote record and paragraph arrays are catalogued so Athena/Spark see them."""
    table = _block("aws_glue_catalog_table", "vfl_documents_clean")
    columns = dict(re.findall(r'name = "([a-z_]+)"\s+type = "([^"]+)"', table))
    assert columns["paragraph_texts"] == "array<string>"
    assert columns["paragraph_pages"] == "array<int>"
    assert columns["votes_against"] == "array<string>"
    assert columns["vote_tally"] == "string" and columns["blocks_dropped"] == "int"


def test_lambda_dockerfile_installs_only_handler_dependencies_and_reuses_the_10b_extractor():
    """D2: Lambda Python 3.12 base, pinned handler deps, no pandas, same extract.py as the pipeline."""
    assert "public.ecr.aws/lambda/python:3.12" in DOCKERFILE
    assert "pymupdf==2.1" in DOCKERFILE and "beautifulsoup4==4.15.0" in DOCKERFILE and "pyarrow==26.0.1" in DOCKERFILE
    assert "pandas" not in DOCKERFILE.split("RUN", 1)[1].split("\n", 1)[0]
    assert "src/volab/text/extract.py" in DOCKERFILE and 'CMD ["handler.handler"]' in DOCKERFILE


def test_lake_copies_are_git_ignored():
    """D3/D4: local lake copies and consumer state never get committed."""
    assert "data/lake/" in (REPO / ".gitignore").read_text(encoding="utf-8").splitlines()
