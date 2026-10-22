"""Static server for the bench pages that also collects page metrics.

POST /report bodies (JSON, one per call) are appended to <results>/pages.jsonl.
Usage: python3 server.py <www-dir> <results-dir> [port]
"""

import json
import sys
import time
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path


class Handler(SimpleHTTPRequestHandler):
    results = None

    def do_POST(self):
        if self.path != "/report":
            self.send_error(404)
            return
        length = int(self.headers.get("Content-Length", 0))
        raw = self.rfile.read(length)
        try:
            record = json.loads(raw)
        except ValueError:
            self.send_error(400)
            return
        record["server_t"] = time.time()
        with open(self.results / "pages.jsonl", "a") as f:
            f.write(json.dumps(record) + "\n")
        self.send_response(204)
        self.end_headers()

    def end_headers(self):
        self.send_header("Cache-Control", "no-store")
        super().end_headers()

    def log_message(self, *args):
        pass


def main():
    www, results = Path(sys.argv[1]), Path(sys.argv[2])
    port = int(sys.argv[3]) if len(sys.argv) > 3 else 8765
    results.mkdir(parents=True, exist_ok=True)
    Handler.results = results
    server = ThreadingHTTPServer(("127.0.0.1", port), partial(Handler, directory=str(www)))
    print(f"serving {www} on http://127.0.0.1:{port}", flush=True)
    server.serve_forever()


if __name__ == "__main__":
    main()
