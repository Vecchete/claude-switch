#!/usr/bin/env python3
"""Stands in for the usage endpoint and for gateways: answers each GET with what ANSWERS holds for its path and
its token, a JSON object {"PATH TOKEN": [STATUS, BODY]} read again at each request, 404 otherwise. Logs
"PATH TOKEN" for each request in LOG, and writes the port it listens on in PORT_FILE.
Usage: fake-usage.py PORT_FILE ANSWERS LOG"""
import http.server
import json
import pathlib
import sys

PORT_FILE, ANSWERS, LOG = (pathlib.Path(argument) for argument in sys.argv[1:4])


class Handler(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        token = self.headers.get("Authorization", "")[len("Bearer "):]
        with LOG.open("a") as log:
            log.write(f"{self.path} {token}\n")
        try:
            answers = json.loads(ANSWERS.read_text())
        except (OSError, ValueError):
            answers = {}
        status, body = answers.get(f"{self.path} {token}", [404, {"error": "not here"}])
        payload = json.dumps(body).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def log_message(self, *arguments):
        pass


server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
PORT_FILE.write_text(str(server.server_address[1]))
server.serve_forever()
