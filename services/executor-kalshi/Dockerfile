FROM python:3.11-slim

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
        gcc libssl-dev \
    && rm -rf /var/lib/apt/lists/*

COPY services/executor-kalshi/requirements.txt /tmp/req.txt
RUN pip install --no-cache-dir -r /tmp/req.txt

COPY shared/ /app/shared/
COPY services/executor-kalshi/src/ /app/

ENV PYTHONPATH=/app
ENV PYTHONUNBUFFERED=1

CMD ["python", "main.py"]
