FROM apache/airflow:3.4.0

USER root

RUN python -m venv /opt/vfl-venv

COPY requirements.txt /opt/vfl-requirements.txt

RUN /opt/vfl-venv/bin/python -m pip install --no-cache-dir \
        --index-url https://download.pytorch.org/whl/cpu torch==2.15.0 \
    && /opt/vfl-venv/bin/python -m pip install --no-cache-dir \
        -r /opt/vfl-requirements.txt

ENV VFL_PYTHON=/opt/vfl-venv/bin/python

USER airflow
