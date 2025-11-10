#!/bin/bash

# Function to update .env file
update_env() {
    local key=$1
    local value=$2
    if grep -q "^$key=" .env; then
        sed -i "s|^$key=.*|$key=$value|" .env
    else
        echo "$key=$value" >> .env
    fi
}

# Load .env file if it exists
if [ -f .env ]; then
    source .env
else
    echo "Creating .env file..."
    touch .env
fi

# Prompt for VIDEO_DIR if not set or doesn't exist
while [ -z "$VIDEO_DIR" ] || [ ! -d "$VIDEO_DIR" ]; do
    if [ -n "$1" ]; then
        VIDEO_DIR="$1"
        shift
    else
        read -p "Enter video directory path: " VIDEO_DIR
    fi
    
    if [ ! -d "$VIDEO_DIR" ]; then
        echo "Error: Directory '$VIDEO_DIR' does not exist"
        VIDEO_DIR=""
    fi
done
update_env "VIDEO_DIR" "$VIDEO_DIR"

# Prompt for DATA_DIR if not set
if [ -z "$DATA_DIR" ]; then
    read -p "Enter data directory path [./data]: " DATA_DIR
    DATA_DIR=${DATA_DIR:-./data}
    update_env "DATA_DIR" "$DATA_DIR"
fi

# Prompt for MAX_WORKERS if not set
if [ -z "$MAX_WORKERS" ]; then
    read -p "Enter number of workers [1]: " MAX_WORKERS
    MAX_WORKERS=${MAX_WORKERS:-1}
    update_env "MAX_WORKERS" "$MAX_WORKERS"
fi

# Set defaults for other variables
[ -z "$DEBUG" ] && update_env "DEBUG" "false"
[ -z "$CACHE_DURATION" ] && update_env "CACHE_DURATION" "3600"

# Create data directory
mkdir -p "$DATA_DIR"

echo "Starting Trickplay Manager..."
echo "Video directory: $VIDEO_DIR"
echo "Data directory: $DATA_DIR"
echo "Workers: $MAX_WORKERS"

# Create venv if it doesn't exist
if [ ! -d "venv" ]; then
    echo "Creating virtual environment..."
    python3 -m venv venv
fi

# Activate venv and install requirements
source venv/bin/activate
pip install -q -r requirements.txt

# Start Flask app
python app.py