#!/bin/bash
# Create venv, install dependencies and run the Flask app
# Usage: ./run.sh [workers]
# Example: ./run.sh 4

WORKERS=${1:-1}  # Default to 1 worker if not specified

python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
python app.py $WORKERS
