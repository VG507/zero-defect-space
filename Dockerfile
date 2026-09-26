FROM python:3.12-slim

WORKDIR /app

# Install dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy source code and contracts
COPY qc/ ./qc/
COPY web/ ./web/
COPY contracts/ ./contracts/
COPY scripts/ ./scripts/
COPY docs/ ./docs/

# Default environment
ENV PYTHONUNBUFFERED=1 \
    QC_BIND=0.0.0.0 \
    QC_PORT=8765

EXPOSE 8765 8766

# Run by default in demo mode
CMD ["python", "-m", "qc.demo"]
