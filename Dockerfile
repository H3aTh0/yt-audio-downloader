# Use an official slim Python image that includes apt
FROM python:3.11-slim

# Install system dependencies (ffmpeg for audio extraction)
RUN apt-get update && \
    apt-get install -y --no-install-recommends ffmpeg && \
    rm -rf /var/lib/apt/lists/*

# Set your working directory
WORKDIR /app

# Copy dependency definitions first (for better caching)
COPY requirements.txt .

# Install Python dependencies (includes yt-dlp)
RUN pip install --upgrade pip && \
    pip install -r requirements.txt

# Copy the rest of your code
COPY . .

# Expose the port and start the FastAPI app
EXPOSE 10000
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "10000"]
