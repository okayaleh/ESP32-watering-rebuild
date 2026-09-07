"""Read-only two-client HTTP soak; log failures, latency and C-heap measurements."""
import argparse
import csv
from datetime import datetime, timezone
import json
import threading
import time
from urllib.request import urlopen

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("url", help="Controller base URL, e.g. http://192.168.1.42")
    parser.add_argument("--seconds", type=int, default=3600)
    parser.add_argument("--interval", type=float, default=5)
    parser.add_argument("--output", default="soak.csv")
    args = parser.parse_args()
    if not args.url.startswith("http://") or args.seconds <= 0 or args.interval < 0.1:
        parser.error("Use an HTTP URL, positive duration and interval >=0.1s")
    until = time.monotonic() + args.seconds
    lock = threading.Lock()
    stop = threading.Event()
    counts = {"ok": 0, "failed": 0}
    with open(args.output, "w", newline="", encoding="utf-8") as stream:
        writer = csv.writer(stream)
        writer.writerow(["utc", "client", "path", "ok", "latency_ms", "idf_free", "idf_largest", "loop_max_ms", "error"])
        def worker(number):
            index = 0
            while not stop.is_set() and time.monotonic() < until:
                path = "/api/status" if index % 6 else "/api/history"
                start = time.monotonic()
                data, error, ok = {}, "", False
                try:
                    with urlopen(args.url.rstrip("/") + path, timeout=15) as response:
                        result = json.load(response)
                    data = result if isinstance(result, dict) else {}
                    ok = True
                except Exception as exc:
                    error = str(exc)[:180]
                with lock:
                    counts["ok" if ok else "failed"] += 1
                    writer.writerow([datetime.now(timezone.utc).isoformat(), number, path, ok,
                        round((time.monotonic() - start) * 1000), data.get("idf_free"), data.get("idf_largest"), data.get("loop_max_ms"), error])
                    stream.flush()
                index += 1
                stop.wait(args.interval)
        threads = [threading.Thread(target=worker, args=(i,)) for i in range(2)]
        for thread in threads: thread.start()
        try:
            while any(thread.is_alive() for thread in threads):
                for thread in threads: thread.join(0.2)
        except KeyboardInterrupt:
            stop.set()
            for thread in threads: thread.join()
    print(json.dumps(counts), "Logged to", args.output)

if __name__ == "__main__": main()
