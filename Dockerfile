# Use latest Python 3.12 slim image
FROM python:3.12-slim

# Set working directory
WORKDIR /usr/src/app
RUN chmod 777 /usr/src/app

# Update and install system dependencies
RUN apt-get update && apt-get upgrade -y && \
    apt-get install -y --no-install-recommends \
    git \
    wget \
    pv \
    jq \
    python3-dev \
    mediainfo \
    gcc \
    libsm6 \
    libxext6 \
    libfontconfig1 \
    libxrender1 \
    libgl1 \
    curl \
    ffmpeg \
    && apt-get clean \
    && rm -rf /var/lib/apt/lists/*

# Copy static ffmpeg binaries (optional, already installed above)
# COPY --from=mwader/static-ffmpeg:6.1 /ffmpeg /bin/ffmpeg
# COPY --from=mwader/static-ffmpeg:6.1 /ffprobe /bin/ffprobe

# Copy bot code
COPY . .

# Upgrade pip and install Python dependencies
RUN pip install --upgrade pip
RUN pip install --no-cache-dir -r requirements.txt

# Expose port (optional, mostly for webhooks)
EXPOSE 5000

# Command to run the bot
CMD ["bash", "run.sh"]
