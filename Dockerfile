# Use official Python 3.12 runtime as a parent image (Matches your local setup)
FROM python:3.12-slim

# Set the working directory in the container
WORKDIR /app

# Install system dependencies (required for some crypto/web3 python packages)
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    python3-dev \
    && rm -rf /var/lib/apt/lists/*

# Copy the requirements file into the container
COPY requirements.txt ./

# Install dependencies
RUN pip install --no-cache-dir -r requirements.txt
# Ensure websocket packages are installed for Binance and Polymarket WS
RUN pip install --no-cache-dir websocket-client websockets

# Copy the rest of the codebase into the container
COPY . .

# Run the sniper bot by default
CMD ["python", "-m", "scalper.oracle_sniper", "--stake", "1.0"]
