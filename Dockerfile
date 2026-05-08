# Build stage (optional, but keep it simple for now as per system_builder scope)
FROM python:3.12-slim-bookworm

# Environment variables
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONPATH=/app

WORKDIR /app

# Install system dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    ca-certificates \
    && rm -rf /var/lib/apt/lists/*

# Install Python dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy source code
COPY . .

# Set permissions (Optional but recommended)
# Note: For development, we might mount the current directory, 
# but for the image itself, we copy and set an entrypoint.

# Default command
CMD ["python", "src/main.py"]
