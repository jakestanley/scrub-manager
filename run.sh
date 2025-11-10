#!/bin/bash
# Create venv, install dependencies and run the Flask app
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
python app.py