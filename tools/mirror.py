"""Local OTA mirror restricted to manifest-listed artifacts; no credentials."""
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
import argparse
from sync_updates import read_manifest

ROOT = Path(__file__).resolve().parents[1]

class Mirror(BaseHTTPRequestHandler):
    def do_GET(self):
        root = getattr(self.server, "directory", ROOT)
        try:
            manifest = read_manifest(root / "build/manifest.json")
        except (OSError, ValueError):
            self.send_error(503, "No valid update manifest")
            return
        allowed = {"/build/manifest.json"} | {"/" + file["path"] for file in manifest["files"]}
        if self.path not in allowed:
            self.send_error(404)
            return
        path = root / self.path.lstrip("/")
        if not path.is_file() or not path.resolve().is_relative_to(root.resolve()):
            self.send_error(404)
            return
        self.send_response(200)
        self.send_header("Content-Type", "application/octet-stream")
        self.send_header("Content-Length", str(path.stat().st_size))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        with path.open("rb") as stream:
            while data := stream.read(8192):
                self.wfile.write(data)

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument("--directory", type=Path, default=ROOT,
                        help="Repository or verified release root containing build/")
    args = parser.parse_args()
    server = HTTPServer(("0.0.0.0", args.port), Mirror)
    server.directory = args.directory.resolve()
    server.serve_forever()
