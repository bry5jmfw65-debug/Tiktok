FROM python:3.12-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PORT=10000 STORAGE_ROOT=/var/data
RUN apt-get update && apt-get install -y --no-install-recommends ffmpeg ca-certificates unzip && rm -rf /var/lib/apt/lists/*
WORKDIR /app
COPY viralclip-ai-complete.zip /tmp/viralclip-ai-complete.zip
RUN unzip -q /tmp/viralclip-ai-complete.zip -d /app && rm /tmp/viralclip-ai-complete.zip
WORKDIR /app/viralclip-ai-complete
RUN pip install --no-cache-dir -r requirements.txt && mkdir -p /var/data/uploads /var/data/clips /var/data/data
EXPOSE 10000
CMD ["sh", "-c", "uvicorn app:app --host 0.0.0.0 --port ${PORT}"]
