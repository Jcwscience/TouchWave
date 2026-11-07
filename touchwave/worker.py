"""Isolated receiver service: reliable commands, disposable live frames."""
import argparse
import gc
import logging
import signal
import time
import zmq
from .devices import SIMULATOR, SoapyProvider, validate_device
from .model import Phase, PROTOCOL_VERSION, ReceiverConfig
from .plugins import ResourceManager, load_plugins
from dataclasses import asdict
from collections import deque
import threading
from .recording import Recorder, replay_info

log = logging.getLogger(__name__)


class Service:
    def __init__(self, root, no_audio=False):
        self.provider = SoapyProvider()
        self.recorder = Recorder(root)
        self.resources = ResourceManager()
        self.config = ReceiverConfig()
        self.selection = SIMULATOR
        self.caps = self.provider.capabilities(SIMULATOR)
        self.phase = Phase.IDLE
        self.error = ""
        self.graph = None
        self.no_audio = no_audio
        self.replay = None
        self.devices = []
        self.started = 0
        self.last_sequence = -1
        self.tool_graph = None
        self.active_tool = None
        self.decoders, self.plugin_errors = load_plugins("decoders")
        self.decoded_events = deque(maxlen=50)
        self.event_lock = threading.Lock()
        self.event_sequence = 0

    def emit(self, event):
        with self.event_lock:
            self.event_sequence += 1
            self.decoded_events.append(dict(sequence=self.event_sequence, event=asdict(event)))

    def start_tool(self, ident):
        if ident not in self.decoders:
            raise ValueError("Decoder plugin is not installed")
        if self.recorder.active:
            raise RuntimeError("Stop recording before opening a decoder tool")
        self.stop()
        self.replay = None
        self.resources.acquire(ident)
        try:
            self.tool_graph = self.decoders[ident].build(self.selection, self.config, self.emit)
            self.tool_graph.start()
            self.active_tool = ident
            self.phase = Phase.RECEIVING
        except Exception:
            self.stop("decoder start failed")
            raise

    def stop(self, reason="user"):
        self.phase = Phase.STOPPING
        if reason != "user":
            self.recorder.discontinuity(reason)
        self.recorder.stop(reason)
        if self.graph:
            graph, self.graph = self.graph, None
            graph.dispose()
            del graph
            gc.collect()
        if self.tool_graph:
            self.tool_graph.stop()
            self.tool_graph.wait()
            self.tool_graph = None
        self.active_tool = None
        self.resources.release(self.resources.owner)
        self.phase = Phase.IDLE

    def start(self):
        if self.graph:
            return
        if self.tool_graph:
            self.stop()
        from .dsp import RadioGraph
        self.resources.acquire("receiver")
        self.phase = Phase.STARTING
        self.error = ""
        try:
            if not self.replay:
                validate_device(self.config, self.caps)
            self.graph = RadioGraph(self.selection, self.config, self.recorder, self.replay, not self.no_audio)
            self.config = self.graph.config
            self.graph.start()
            self.started = time.monotonic()
            self.last_sequence = -1
            self.phase = Phase.RECEIVING
        except Exception:
            self.stop("start failed")
            self.phase = Phase.ERROR
            raise

    def configure(self, config):
        old = self.config.to_dict()
        changed = {k for k, value in config.to_dict().items() if old[k] != value}
        live = {"volume", "muted", "squelch", "force_mono", "step"}
        if self.recorder.active and changed - live:
            raise RuntimeError("Stop the recording before changing tuning or device settings")
        if self.replay and changed & {"frequency", "sample_rate"}:
            raise ValueError("Replay uses the capture's frequency and sample rate")
        if not self.replay:
            validate_device(config, self.caps)
        running = self.graph is not None
        if running and changed - live:
            self.stop()
        self.config = config
        if running and not self.graph:
            self.start()
        if self.graph:
            self.graph.set_audio(config)

    def command(self, request):
        if request.get("version") != PROTOCOL_VERSION:
            raise ValueError("Incompatible worker protocol")
        op = request["op"]
        args = request.get("args", {})
        if op == "tool_start":
            self.start_tool(args["id"])
            return self.status()
        if op == "discover":
            self.devices = self.provider.discover() + [SIMULATOR]
            return self.devices
        if op == "select":
            if self.recorder.active:
                raise RuntimeError("Stop recording before switching device")
            self.stop()
            selection = args["device"]
            caps = self.provider.capabilities(selection)
            self.selection, self.caps, self.replay = selection, caps, None
            rate = 8000000 if selection["driver"] == "hackrf" else 2400000
            rates = caps["sample_rates"]
            if rates:
                rate = min(rates, key=lambda r: abs(r-rate))
            lo, hi = caps["gain_range"]
            frequency = self.config.frequency
            if not any(a <= frequency <= b for a, b in caps["frequency_ranges"]):
                frequency = int(caps["frequency_ranges"][0][0])
            self.config = self.config.changed(sample_rate=int(rate), gain=min(max(self.config.gain, lo), hi), frequency=frequency,
                                              gain_stages={}, antenna="", biastee=False,
                                              ppm=self.config.ppm if caps["ppm"] else 0)
            return caps
        if op == "configure":
            self.configure(ReceiverConfig.from_dict(args["config"]))
        elif op == "start":
            self.start()
        elif op == "stop":
            self.stop()
        elif op == "record":
            if not self.graph or self.replay:
                raise RuntimeError("Start live reception before recording")
            return self.recorder.start(args["kind"], self.config, float(args.get("duration", 60)),
                                       int(args.get("max_bytes", 256*1024**2)), self.selection["driver"])
        elif op == "record_stop":
            return self.recorder.stop()
        elif op == "replay":
            if self.recorder.active:
                raise RuntimeError("Stop recording before replay")
            self.stop()
            path, rate, freq = replay_info(args["path"])
            self.replay = path
            self.config = self.config.changed(sample_rate=rate, frequency=freq)
            self.start()
        elif op == "live":
            self.stop()
            self.replay = None
            self.command(dict(version=1, op="select", args={"device": self.selection}))
        elif op != "ping":
            raise ValueError(f"Unknown command: {op}")
        return self.status()

    def status(self):
        meter = self.graph.meter if self.graph else None
        with self.event_lock:
            decoded = list(self.decoded_events)
        return dict(state_time=time.monotonic(), version=PROTOCOL_VERSION, phase=str(self.phase), error=self.error,
                    config=self.config.to_dict(), device=self.selection, capabilities=self.caps,
                    level=meter.level if meter else -140, pilot=meter.pilot if meter else False,
                    recording=self.recorder.status(), completed=self.recorder.completed[-20:],
                    replay=str(self.replay) if self.replay else None, active_tool=self.active_tool,
                    decoded_events=decoded, plugin_errors=self.plugin_errors,
                    overruns=None, telemetry=telemetry())

    def frame(self):
        if not self.graph:
            return None
        self.graph.set_audio(self.config)
        frame = self.graph.spectrum.latest()
        now = time.monotonic()
        if frame and frame[0] != self.last_sequence:
            self.last_sequence = frame[0]
            return dict(type="spectrum", db=frame[1].tolist(), frequency=self.config.frequency,
                        rate=self.graph.rate, timestamp=frame[2])
        last = frame[2] if frame else self.started
        if now - last > 4:
            reason = "Replay finished" if self.replay else "No samples received; check the SDR connection"
            self.stop(reason)
            if not self.replay:
                self.phase, self.error = Phase.ERROR, reason
        return None


def telemetry():
    try:
        from pathlib import Path
        temp = float(Path("/sys/class/thermal/thermal_zone0/temp").read_text()) / 1000
    except (OSError, ValueError):
        temp = None
    import os
    return {"temperature": temp, "load": os.getloadavg()[0]}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--commands", required=True)
    parser.add_argument("--events", required=True)
    parser.add_argument("--recordings", required=True)
    parser.add_argument("--no-audio", action="store_true")
    args = parser.parse_args()
    from .lifecycle import follow_parent
    follow_parent()
    context = zmq.Context()
    reply = context.socket(zmq.REP)
    reply.linger = 0
    reply.bind(args.commands)
    events = context.socket(zmq.PUB)
    events.linger = 0
    events.sndhwm = 4
    events.bind(args.events)
    service = Service(args.recordings, args.no_audio)
    running = True

    def shutdown(*_):
        nonlocal running
        running = False
    signal.signal(signal.SIGTERM, shutdown)
    signal.signal(signal.SIGINT, shutdown)
    last_status = 0
    try:
        while running:
            if reply.poll(20):
                request = reply.recv_json()
                try:
                    if request["op"] == "shutdown":
                        running = False
                        result = None
                    else:
                        service.error = ""
                        result = service.command(request)
                    reply.send_json(dict(ok=True, result=result, state=service.status()))
                except Exception as exc:
                    log.exception("Receiver command failed")
                    if request.get("op") == "start":
                        service.phase = Phase.ERROR
                    service.error = str(exc)
                    reply.send_json(dict(ok=False, error=str(exc), state=service.status()))
            try:
                frame = service.frame()
                if frame:
                    events.send_json(frame, flags=zmq.DONTWAIT)
                if time.monotonic() - last_status >= 0.2:
                    events.send_json(dict(type="state", **service.status()), flags=zmq.DONTWAIT)
                    last_status = time.monotonic()
            except zmq.Again:
                pass
    finally:
        service.stop("worker shutdown")
        reply.close()
        events.close()
        context.term()


if __name__ == "__main__":
    main()
