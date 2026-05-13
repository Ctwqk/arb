FROM python:3.11-slim

WORKDIR /app

ARG TORCH_INDEX_URL=https://download.pytorch.org/whl/cpu
ARG TORCH_VERSION=2.5.1

RUN apt-get update && apt-get install -y --no-install-recommends \
        gcc g++ \
    && rm -rf /var/lib/apt/lists/*

COPY services/resolver/requirements.txt /tmp/req.txt
RUN pip install --no-cache-dir --upgrade pip \
    && pip install --no-cache-dir --index-url "${TORCH_INDEX_URL}" "torch==${TORCH_VERSION}" \
    && pip install --no-cache-dir -r /tmp/req.txt

# Pre-download the embedding model at build time for faster startup
RUN python -c "from sentence_transformers import SentenceTransformer; SentenceTransformer('all-MiniLM-L6-v2')"

COPY shared/ /app/shared/
COPY services/resolver/src/ /app/

ENV PYTHONPATH=/app
ENV PYTHONUNBUFFERED=1

CMD ["python", "main.py"]
