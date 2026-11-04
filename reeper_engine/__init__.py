"""The engine behind the Reeper Mac app.

The app starts `python -m reeper_engine` once and talks to it over stdin/stdout, one JSON
object per line. See server.py for the protocol and handlers.py for the methods.
"""

PROTOCOL_VERSION = 1
