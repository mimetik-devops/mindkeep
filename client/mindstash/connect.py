"""Signing a machine in through the browser.

The machine opens the website's connect page with a port and a nonce, and listens on
that port. The person, already signed in there, clicks *Connect*; the site creates a
device token and sends the browser to `http://127.0.0.1:<port>/?token=…&nonce=…`,
which this module answers. The same flow `gh auth login` uses — nothing is typed.

Loopback only: the listener binds 127.0.0.1, and a request without the nonce it was
started with is ignored, so a page that guesses the port learns nothing and plants
nothing.

Shared by the CLI (`mindstash login`) and the tray app.
"""

import json
import secrets
import socket
import threading
import urllib.parse
import urllib.request
import webbrowser
from collections.abc import Callable
from http.server import BaseHTTPRequestHandler, HTTPServer

DONE = b"""<!doctype html><meta charset="utf-8"><title>Mindstash</title>
<body style="font-family:system-ui;padding:40px"><h2>Connected.</h2>
<p>You can close this tab and go back to Mindstash.</p></body>"""


def about(server: str) -> dict:
    """What the API says about itself — the web address, for the connect page."""
    with urllib.request.urlopen(server.rstrip("/") + "/about") as response:
        return json.loads(response.read())


def connect_url(web: str, port: int, nonce: str, name: str) -> str:
    query = urllib.parse.urlencode({"port": port, "nonce": nonce, "name": name})
    return web.rstrip("/") + "/?connect=1&" + query


def sign_in(
    web: str,
    name: str,
    open_browser: Callable[[str], object] = webbrowser.open,
    timeout: float = 300,
) -> str:
    """Open the connect page for this machine and wait for the token it sends back.

    `name` is what the device is called on the website (the hostname, usually). Raises
    TimeoutError when nobody clicks within `timeout` seconds.
    """
    nonce = secrets.token_urlsafe(16)
    got: dict[str, str] = {}
    done = threading.Event()

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:  # noqa: N802 - the http.server contract
            query = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
            token = query.get("token", [""])[0]
            if query.get("nonce", [""])[0] != nonce or not token:
                self.send_error(404)
                return
            got["token"] = token
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.end_headers()
            self.wfile.write(DONE)
            done.set()

        def log_message(self, *_: object) -> None:  # quiet: it is a one-shot listener
            pass

    server = HTTPServer(("127.0.0.1", 0), Handler)
    port = server.server_address[1]
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        open_browser(connect_url(web, port, nonce, name))
        if not done.wait(timeout):
            raise TimeoutError("nobody connected this device in time")
        return got["token"]
    finally:
        server.shutdown()
        server.server_close()


def hostname() -> str:
    return socket.gethostname() or "this machine"
