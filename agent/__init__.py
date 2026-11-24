"""dealgrep research agent.

Runs hourly in GitHub Actions: pulls public deal feeds, optionally asks Claude
to research live deals on the web, normalises everything into one JSON file
that the static site renders client-side.
"""

__version__ = "0.1.0"
