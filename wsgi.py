"""
wsgi.py — Gunicorn entry point for the AEAP Reference Consumer Agent.

Adds the shared/ and parent directories to the Python path so
aeap_client.py is available without installing it as a package.
"""
import sys
import os

# Add shared module and parent directories to path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..', 'shared'))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from app import app

if __name__ == '__main__':
    app.run()
