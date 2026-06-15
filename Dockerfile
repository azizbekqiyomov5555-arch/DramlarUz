# ===== STAGE 1: telegram-bot-api server (C++ build) =====
FROM alpine:3.20 AS tdbuilder

RUN apk add --no-cache \
    alpine-sdk linux-headers git zlib-dev openssl-dev gperf cmake

WORKDIR /src
RUN git clone --recursive --depth=1 https://github.com/tdlib/telegram-bot-api.git
RUN cd telegram-bot-api && rm -rf build && mkdir build && cd build && \
    cmake -DCMAKE_BUILD_TYPE=Release -DCMAKE_INSTALL_PREFIX:PATH=/usr .. && \
    cmake --build . --target install -j$(nproc)

# ===== STAGE 2: Python runtime + bot =====
FROM python:3.11-slim

RUN apt-get update && apt-get install -y --no-install-recommends \
        ffmpeg supervisor ca-certificates libstdc++6 \
    && rm -rf /var/lib/apt/lists/*

# Lokal Telegram Bot API binary
COPY --from=tdbuilder /usr/bin/telegram-bot-api /usr/local/bin/telegram-bot-api

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY bot.py .
COPY supervisord.conf /etc/supervisor/conf.d/supervisord.conf

# Telegram Bot API ma'lumotlari uchun papka
RUN mkdir -p /var/lib/telegram-bot-api

EXPOSE 8080 8081

CMD ["/usr/bin/supervisord", "-c", "/etc/supervisor/conf.d/supervisord.conf"]
