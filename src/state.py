"""Bounded telemetry. Compact live samples; long history streams from flash."""
import os
from compat import json, epoch, ticks_diff, ticks_ms
from persistence import exists

class State:
    def __init__(self, root="."):
        self.root = root
        self.events_ring = []
        self.pending_events = []
        self.event_retry_ms = None
        self.live = []
        self.last_live = None
        self.last_flash = None
        self.pending_flash = None
        self.flash_retry_ms = None
        self.names = ()
        self.last_prune = None
        self.prune_job = None
        for filename in ("events.log.prev", "events.log"):
            try:
                with open(self.root + "/" + filename) as stream:
                    for line in stream:
                        try:
                            item = json.loads(line)
                            if isinstance(item, dict) and isinstance(item.get("message"), str):
                                self.events_ring.append(item)
                                if len(self.events_ring) > 64:
                                    self.events_ring.pop(0)
                        except ValueError:
                            pass
            except OSError:
                pass

    def event(self, kind, message=""):
        entry = {"t": epoch(), "type": str(kind)[:32], "message": str(message)[:160]}
        self.events_ring.append(entry)
        if len(self.events_ring) > 64:
            self.events_ring.pop(0)
        # Keep references to the same bounded entries. A valve-open callback
        # must never start a LittleFS operation while an output is energized.
        self.pending_events.append(entry)
        if len(self.pending_events) > 64:
            self.pending_events.pop(0)
        print(entry["type"] + ": " + entry["message"])

    def flush_events(self, now_ms=None):
        """Append at most one queued event; caller must confirm outputs idle."""
        if not self.pending_events:
            return False
        now_ms = ticks_ms() if now_ms is None else now_ms
        if self.event_retry_ms is not None and ticks_diff(now_ms, self.event_retry_ms) < 5000:
            return False
        entry = self.pending_events[0]
        try:
            path = self.root + "/events.log"
            if exists(path) and os.stat(path)[6] > 32768:
                if exists(path + ".prev"):
                    os.remove(path + ".prev")
                os.rename(path, path + ".prev")
            with open(path, "a") as f:
                f.write(json.dumps(entry) + "\n")
        except OSError:
            self.event_retry_ms = now_ms
            return False  # Retain the event; avoid storage-failure retry storms.
        self.event_retry_ms = None
        self.pending_events.pop(0)
        return True

    def sample(self, readings, now_ms, now_epoch, synced, allow_flash=True):
        if self.last_live is None or ticks_diff(now_ms, self.last_live) >= 60000:
            self.last_live = now_ms
            names = tuple(readings.keys())
            if names != self.names:
                self.names = names
            values = bytes(255 if readings[n].get("percent") is None or readings[n].get("error")
                           else min(200, max(0, int(readings[n]["percent"] * 2))) for n in names)
            point = (now_epoch if synced else None, self.names, values, now_ms)
            self.live.append(point)
            if len(self.live) > 180:
                self.live.pop(0)
            if synced and (self.last_flash is None or ticks_diff(now_ms, self.last_flash) >= 900000):
                # Replace a deferred snapshot with the latest compact sample.
                # It shares its object with live history and remains bounded.
                self.pending_flash = point
        if (not allow_flash or self.pending_flash is None or self.prune_job is not None or
                (self.flash_retry_ms is not None and ticks_diff(now_ms, self.flash_retry_ms) < 5000)):
            return
        # Check the pending write on every poll, including between live sample
        # intervals, so closure permits prompt persistence of the saved point.
        try:
            with open(self.root + "/history.jsonl", "a") as f:
                f.write(json.dumps(self._expand(self.pending_flash, now_epoch, now_ms)) + "\n")
        except OSError:
            self.flash_retry_ms = now_ms
            raise
        self.last_flash = now_ms
        self.pending_flash = None
        self.flash_retry_ms = None

    def _expand(self, point, now_epoch, now_ms):
        stamp, names, values, sampled = point
        return {"t": stamp if stamp is not None else now_epoch - max(0, ticks_diff(now_ms, sampled)) // 1000,
                "readings": [{"name": n, "percent": None if values[i] == 255 else values[i] / 2} for i, n in enumerate(names)]}

    def history(self, hours=None, now_epoch=None, now_ms=0):
        now_epoch = now_epoch if now_epoch is not None else epoch()
        if hours is None:
            # Single-threaded poller advances this iterator between samples.
            for point in self.live:
                yield self._expand(point, now_epoch, now_ms)
            return
        cutoff = now_epoch - min(336, max(1, hours)) * 3600
        # Retain readability of the original project's saved CSV history.
        # The original file is preserved unchanged during migration.
        for point in self._records("history.csv", legacy=True):
            yield point if point and cutoff <= point["t"] <= now_epoch else None
        for point in self._records("history.jsonl"):
            yield point if point and cutoff <= point["t"] <= now_epoch else None

    def _records(self, filename, legacy=False):
        path = self.root + "/" + filename
        if not exists(path) and exists(path + ".prev"):
            path += ".prev"
        try:
            with open(path) as f:
                for line in f:
                    try:
                        if legacy:
                            fields = line.strip().split(",")
                            stamp = int(fields[0])
                            if 600000000 <= stamp < 946684800:
                                stamp += 946684800
                            values = []
                            for field in fields[1:]:
                                n, raw = field.rsplit("=", 1)
                                values.append({"name": n, "percent": None if raw == "None" else float(raw)})
                            point = {"t": stamp, "readings": values}
                        else:
                            point = json.loads(line)
                        if not isinstance(point.get("t"), int) or not isinstance(point.get("readings"), list):
                            raise ValueError("Invalid history point")
                        yield point
                    except (ValueError, KeyError, TypeError):
                        yield None  # Cooperative transport checkpoint.
        except OSError:
            return

    def prune(self, now_epoch):
        if self.prune_job is None:
            if self.last_prune is not None and now_epoch - self.last_prune < 86400:
                return
            self.prune_job = self._prune_records(now_epoch)
        try:
            next(self.prune_job)
        except StopIteration:
            self.prune_job = None
            self.last_prune = now_epoch
        except Exception:
            self.prune_job.close()
            self.prune_job = None
            self.last_prune = now_epoch  # Avoid flash-error retry storms.
            raise

    def _prune_records(self, now_epoch):
        path = self.root + "/history.jsonl"
        if not exists(path) and not exists(path + ".prev"):
            return
        with open(path + ".tmp", "w") as output:
            for point in self._records("history.jsonl"):
                if point and now_epoch - 604800 <= point["t"] <= now_epoch:
                    output.write(json.dumps(point) + "\n")
                yield  # At most one record processed per supervisor turn.
            output.flush()
        if exists(path):
            if exists(path + ".prev"):
                os.remove(path + ".prev")
            os.rename(path, path + ".prev")
        os.rename(path + ".tmp", path)
