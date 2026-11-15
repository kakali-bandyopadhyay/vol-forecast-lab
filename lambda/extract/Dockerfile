# vfl-extract Lambda image (Spec_vfl-step10c D2). Build from the REPO ROOT:
#   docker build --platform linux/amd64 -f lambda/extract/Dockerfile -t vfl-extract:local .
# Base image: AWS Lambda Python 3.12. Pin by digest after the first pull (see lambda/build_push.ps1 -PinBase).
ARG BASE_IMAGE=public.ecr.aws/lambda/python:3.12
FROM ${BASE_IMAGE}

# Handler dependencies only (no pandas): PDF/HTML extraction + Parquet writing.
RUN pip install --no-cache-dir pymupdf==2.1 beautifulsoup4==4.15.0 pyarrow==26.0.1

# The extractor is the same module the 10b pipeline uses, so lake text == documents.parquet text.
COPY src/volab/__init__.py ${LAMBDA_TASK_ROOT}/volab/__init__.py
COPY src/volab/text/__init__.py ${LAMBDA_TASK_ROOT}/volab/text/__init__.py
COPY src/volab/text/extract.py ${LAMBDA_TASK_ROOT}/volab/text/extract.py
COPY lambda/extract/handler.py ${LAMBDA_TASK_ROOT}/handler.py

CMD ["handler.handler"]
