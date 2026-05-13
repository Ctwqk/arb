FROM python:3.11-slim

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
        gcc g++ libssl-dev \
    && rm -rf /var/lib/apt/lists/*

COPY services/executor-polymarket/requirements.txt /tmp/req.txt
RUN pip install --no-cache-dir -r /tmp/req.txt

COPY shared/ /app/shared/
COPY services/executor-polymarket/src/ /app/

ENV PYTHONPATH=/app
ENV PYTHONUNBUFFERED=1

# This container is launched with --network=host inside vpn-polymarket namespace.
# REDIS_HOST must be set to the veth bridge IP (e.g. 192.168.100.1) so that
# Redis (running in the main namespace) is reachable.
CMD ["python", "main.py"]
