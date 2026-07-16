"""
wsgi.py — Gunicorn entry point for the Nustro Reference Consumer Agent.

Adds the shared/ and parent directories to the Python path so
aeap_client.py is available without installing it as a package.

Dev: `python wsgi.py` serves the local console on http://localhost:5002.
Prod: `gunicorn wsgi:app`.
"""
import sys
import os

# Add shared module and parent directories to path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..', 'shared'))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from app import app

if __name__ == '__main__':
    # Explicit port — a bare app.run() would default to 5000 and collide with
    # nothing here, but the Provider owns 5001 and the docs/console assume 5002.
    app.run(host='0.0.0.0', port=int(os.environ.get('PORT', 5002)), debug=False)
