FROM python:3.12-slim

WORKDIR /app
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1

COPY pyproject.toml README.md ./
COPY sentinela ./sentinela
COPY config ./config
COPY data ./data

RUN pip install --no-cache-dir -e '.[bot]'

CMD ["python", "-m", "sentinela.bot"]
