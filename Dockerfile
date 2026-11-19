FROM python:3.12-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PORT=10000 STORAGE_ROOT=/var/data
RUN apt-get update && apt-get install -y --no-install-recommends ffmpeg ca-certificates && rm -rf /var/lib/apt/lists/*
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY app.py .
COPY static ./static
RUN mkdir -p /var/data/uploads /var/data/clips
EXPOSE 10000
CMD ["sh","-c","uvicorn app:app --host 0.0.0.0 --port ${PORT}"]
