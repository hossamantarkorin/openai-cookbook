FROM python:3.12-slim

WORKDIR /app
RUN apt-get update && apt-get install -y --no-install-recommends tzdata && rm -rf /var/lib/apt/lists/*
ENV TZ=America/New_York

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY smallcap/ ./smallcap/
COPY scripts/ ./scripts/

RUN mkdir -p smallcap/logs

CMD ["python", "-u", "smallcap/scheduler.py"]
