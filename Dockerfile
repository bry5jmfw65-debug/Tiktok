FROM python:3.12-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PORT=10000 STORAGE_ROOT=/var/data
RUN apt-get update && apt-get install -y --no-install-recommends ffmpeg ca-certificates curl unzip && rm -rf /var/lib/apt/lists/*
RUN curl -fsSL https://deno.land/install.sh | sh && mv /root/.deno/bin/deno /usr/local/bin/deno
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY app.py .
COPY static ./static
RUN mkdir -p /var/data/uploads /var/data/clips
EXPOSE 10000
CMD ["sh","-c","uvicorn app:app --host 0.0.0.0 --port ${PORT}"]
