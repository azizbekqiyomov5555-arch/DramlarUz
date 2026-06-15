FROM python:3.11-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    DEBIAN_FRONTEND=noninteractive

# ffmpeg watermark uchun shart
RUN apt-get update && apt-get install -y --no-install-recommends \
        ffmpeg ca-certificates fonts-dejavu-core \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# Railway shu portni eshitishi mumkin (health server)
ENV PORT=8080
EXPOSE 8080

CMD ["python", "-u", "bot.py"]
