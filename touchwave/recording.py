"""Sample-bounded WAV/SigMF capture, shared safely with DSP sink threads."""
import json
import shutil
import threading
import time
import wave
from datetime import datetime, timezone
from pathlib import Path
import numpy as np


class Recorder:
    RESERVE = 128 * 1024**2

    def __init__(self, root):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)
        self.lock = threading.RLock()
        self.session = None
        self.completed = []

    @property
    def active(self):
        return self.session is not None

    def start(self, kind, config, duration=60, max_bytes=256 * 1024**2, source="unknown"):
        if kind not in ("audio", "iq"):
            raise ValueError("Recording type must be audio or iq")
        if not 1 <= duration <= 3600 or not 1024 <= max_bytes <= 2 * 1024**3:
            raise ValueError("Recording limit out of range (1–3600 s, up to 2 GiB)")
        with self.lock:
            if self.active:
                raise RuntimeError("A recording is already active")
            free = shutil.disk_usage(self.root).free - self.RESERVE
            if free < 1024 * 1024:
                raise RuntimeError("Insufficient free disk space")
            rate = config.sample_rate if kind == "iq" else 48000
            stride = 8 if kind == "iq" else 4
            limit = int(min(duration * rate, max_bytes // stride, free // stride))
            stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S.%fZ")
            path = self.root / (stamp + (".sigmf-data" if kind == "iq" else ".wav"))
            handle = path.open("wb") if kind == "iq" else wave.open(str(path), "wb")
            if kind == "audio":
                handle.setnchannels(2)
                handle.setsampwidth(2)
                handle.setframerate(48000)
            self.session = dict(kind=kind, path=str(path), rate=rate, count=0, limit=limit,
                                started=time.time(), config=config.to_dict(), handle=handle,
                                source=source, discontinuities=[], disk_check=0)
            return self.status()

    def status(self):
        with self.lock:
            if not self.active:
                return None
            s = self.session
            return dict(kind=s["kind"], path=s["path"], seconds=s["count"] / s["rate"],
                        limit_seconds=s["limit"] / s["rate"])

    def write(self, kind, samples):
        with self.lock:
            s = self.session
            if not s or s["kind"] != kind:
                return
            try:
                if time.monotonic() > s["disk_check"]:
                    s["disk_check"] = time.monotonic() + 0.5
                    if shutil.disk_usage(self.root).free <= self.RESERVE:
                        self.stop("low disk space")
                        return
                count = min(len(samples), s["limit"] - s["count"])
                if kind == "iq":
                    s["handle"].write(np.asarray(samples[:count], dtype="<c8").tobytes())
                else:
                    pcm = (np.clip(samples[:count], -1, 1) * 32767).astype("<i2")
                    s["handle"].writeframesraw(pcm.tobytes())
                s["count"] += count
                if s["count"] >= s["limit"]:
                    self.stop("limit reached")
            except OSError as exc:
                self.stop(f"write error: {exc}")

    def discontinuity(self, reason):
        with self.lock:
            if self.active:
                self.session["discontinuities"].append(dict(time=time.time(), reason=reason))

    def stop(self, reason="user"):
        with self.lock:
            if not self.active:
                return None
            s, self.session = self.session, None
            try:
                s["handle"].close()
            except OSError as exc:
                reason = f"close error: {exc}"
            meta = {k: v for k, v in s.items() if k not in ("handle", "disk_check", "limit")}
            meta.update(seconds=s["count"] / s["rate"], reason=reason,
                        overrun_reporting="Source may not report all hardware overruns")
            path = Path(s["path"])
            try:
                if s["kind"] == "iq":
                    doc = {"global": {"core:datatype": "cf32_le", "core:sample_rate": s["rate"],
                                      "core:version": "1.0.0", "core:recorder": "TouchWave",
                                      "core:extensions": [{"name": "touchwave", "version": "0.1.0", "optional": True}],
                                      "core:description": "TouchWave receive capture",
                                      "touchwave:recording": meta},
                           "captures": [{"core:sample_start": 0,
                                         "core:frequency": s["config"]["frequency"],
                                         "core:datetime": datetime.fromtimestamp(s["started"], timezone.utc).isoformat()}],
                           "annotations": []}
                    path.with_suffix(".sigmf-meta").write_text(json.dumps(doc, indent=2))
                else:
                    path.with_suffix(".json").write_text(json.dumps(meta, indent=2))
            except OSError as exc:
                meta["metadata_error"] = str(exc)
            self.completed.append(meta)
            return meta


def replay_info(path):
    path = Path(path)
    if path.suffix == ".sigmf-meta":
        path = path.with_suffix(".sigmf-data")
    doc = json.loads(path.with_suffix(".sigmf-meta").read_text())
    if doc["global"]["core:datatype"] != "cf32_le":
        raise ValueError("Replay supports cf32_le SigMF captures")
    if path.stat().st_size == 0 or path.stat().st_size % 8:
        raise ValueError("IQ data is empty or truncated")
    return path, int(doc["global"]["core:sample_rate"]), int(doc["captures"][0]["core:frequency"])
