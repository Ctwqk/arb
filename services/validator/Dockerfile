FROM python:3.11-slim

WORKDIR /app

COPY services/validator/requirements.txt .
RUN pip install --no-cache-dir --upgrade pip \
    && pip install --no-cache-dir -r requirements.txt

COPY shared/ /app/shared/
COPY services/validator/src/ /app/

ENV PYTHONUNBUFFERED=1

CMD ["python", "main.py"]
