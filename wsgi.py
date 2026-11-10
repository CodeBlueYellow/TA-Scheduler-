# Entry point for web hosts (Alwaysdata, PythonAnywhere, gunicorn, etc.).
# Hosts look for a variable named "application", so this hands them the Flask app.
# For local testing keep using: python app.py
import os
import sys

# Make sure app.py is importable even if the host starts in another folder
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from app import app as application
