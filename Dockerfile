FROM python:3.12-slim AS builder
WORKDIR /build
COPY requirements.txt .
RUN python -m venv /opt/venv && /opt/venv/bin/pip install --no-cache-dir -r requirements.txt

FROM python:3.12-slim
ENV PATH="/opt/venv/bin:$PATH" PYTHONUNBUFFERED=1 QC_BIND=0.0.0.0 QC_PORT=8765 QC_DB_PATH=/data/qc.db
COPY --from=builder /opt/venv /opt/venv
WORKDIR /app
COPY qc/ ./qc/
COPY web/ ./web/
COPY contracts/ ./contracts/
RUN groupadd -r qc && useradd -r -g qc qc && mkdir /data && chown qc:qc /data
USER qc
EXPOSE 8765 8766
CMD ["python", "-m", "qc.server"]
