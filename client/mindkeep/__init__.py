"""Mindkeep desktop client: the sync engine, a CLI, and a tray app."""

# 0.0.0 in git: the release workflow stamps the tag's number here and in pyproject.toml
# (see stamp.py), so the app can say which release it is.
__version__ = "0.0.0"

# Sent with every request. Python's default ("Python-urllib/3.x") is on the block list
# of Cloudflare's browser check, which fronts mindkeep.io: the requests never reached
# the API. A client that says who it is gets through — and shows up as itself in logs.
USER_AGENT = f"Mindkeep/{__version__} (+https://mindkeep.io)"
