"""Nonblocking UI bridge. Each ZMQ socket belongs to exactly one IO thread."""
import os
from pathlib import Path
import queue
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import zmq
from .model import PROTOCOL_VERSION


class WorkerClient:
    def __init__(self, recordings, no_audio=False):
        self.root = Path(tempfile.mkdtemp(prefix="touchwave-"))
        self.commands = "ipc://" + str(self.root / "commands")
        self.events = "ipc://" + str(self.root / "events")
        self.log_path = Path(recordings).parent / "worker.log"
        if self.log_path.exists() and self.log_path.stat().st_size > 2*1024**2:
            self.log_path.replace(self.log_path.with_suffix(".log.1"))
        self.log = self.log_path.open("a")
        self.log.write("\n--- Starting receiver worker ---\n")
        self.log.flush()
        env = os.environ.copy()
        package_root = str(Path(__file__).resolve().parent.parent)
        env["PYTHONPATH"] = package_root + os.pathsep + env.get("PYTHONPATH", "")
        args = [sys.executable, "-m", "touchwave.worker", "--commands", self.commands,
                "--events", self.events, "--recordings", str(recordings)]
        if no_audio:
            args.append("--no-audio")
        self.process = subprocess.Popen(args, env=env, stdout=self.log, stderr=self.log)
        self.pending = queue.Queue(maxsize=32)
        self.responses = queue.Queue()
        self.latest = {}
        self.lock = threading.Lock()
        self.closed = threading.Event()
        self.context = zmq.Context()
        self.threads = [threading.Thread(target=self._requests, daemon=True),
                        threading.Thread(target=self._events, daemon=True)]
        for thread in self.threads:
            thread.start()

    def send(self, op, callback=None, **args):
        try:
            self.pending.put_nowait((dict(version=PROTOCOL_VERSION, op=op, args=args), callback))
        except queue.Full:
            self.responses.put((callback, dict(ok=False, error="Receiver is busy; try again")))

    def _requests(self):
        sock = self.context.socket(zmq.REQ)
        sock.linger = 0
        sock.connect(self.commands)
        try:
            while not self.closed.is_set():
                try:
                    message, callback = self.pending.get(timeout=0.1)
                except queue.Empty:
                    continue
                sock.send_json(message)
                deadline = time.monotonic() + 15
                received = False
                while not self.closed.is_set() and time.monotonic() < deadline:
                    if sock.poll(100):
                        result = sock.recv_json()
                        received = True
                        self.responses.put((callback, result))
                        break
                    if self.process.poll() is not None:
                        break
                else:
                    result = None
                if not received:
                    self.responses.put((callback, dict(ok=False, error="Receiver worker stopped responding; restart it in Settings")))
                    # A timeout leaves REQ unable to send. Replace the socket.
                    sock.close()
                    sock = self.context.socket(zmq.REQ)
                    sock.linger = 0
                    sock.connect(self.commands)
        finally:
            sock.close()

    def _events(self):
        sock = self.context.socket(zmq.SUB)
        sock.linger = 0
        sock.rcvhwm = 4
        sock.setsockopt_string(zmq.SUBSCRIBE, "")
        sock.connect(self.events)
        try:
            while not self.closed.is_set():
                if sock.poll(100):
                    message = sock.recv_json()
                    with self.lock:
                        self.latest[message["type"]] = message
        finally:
            sock.close()

    def drain(self):
        with self.lock:
            messages, self.latest = self.latest, {}
        replies = []
        while True:
            try:
                replies.append(self.responses.get_nowait())
            except queue.Empty:
                break
        return messages, replies

    def close(self):
        self.closed.set()
        if self.process.poll() is None:
            self.process.terminate()
        try:
            self.process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            self.process.kill()
            self.process.wait()
        for thread in self.threads:
            thread.join(timeout=1)
        self.context.term()
        self.log.close()
        shutil.rmtree(self.root, ignore_errors=True)
