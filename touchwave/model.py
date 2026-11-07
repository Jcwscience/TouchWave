"""Serializable receiver state shared by the UI, worker and extensions."""
from dataclasses import asdict, dataclass, field, fields, replace
from enum import StrEnum
import math

PROTOCOL_VERSION = 1


class Phase(StrEnum):
    DISCONNECTED = "disconnected"
    IDLE = "idle"
    STARTING = "starting"
    RECEIVING = "receiving"
    STOPPING = "stopping"
    ERROR = "error"


MODES = {
    "WFM": {"label": "FM stereo", "bandwidth": 180000, "rate": 240000},
    "NFM": {"label": "Narrow FM", "bandwidth": 12500, "rate": 48000},
    "AM": {"label": "AM", "bandwidth": 10000, "rate": 48000},
    "USB": {"label": "Upper sideband", "bandwidth": 2800, "rate": 48000},
    "LSB": {"label": "Lower sideband", "bandwidth": 2800, "rate": 48000},
}


@dataclass
class ReceiverConfig:
    frequency: int = 100100000
    mode: str = "WFM"
    bandwidth: int = 180000
    step: int = 100000
    sample_rate: int = 2400000
    gain: float = 20.0
    gain_stages: dict = field(default_factory=dict)
    antenna: str = ""
    biastee: bool = False
    ppm: float = 0.0
    squelch: float = -100.0
    volume: float = 0.35
    muted: bool = False
    agc: bool = True
    deemphasis: int = 75
    force_mono: bool = False
    audio_device: str = "default"
    economy: bool = False

    def to_dict(self):
        return asdict(self)

    @classmethod
    def from_dict(cls, values):
        if not isinstance(values, dict):
            raise ValueError("Receiver configuration must be an object")
        names = {f.name for f in fields(cls)}
        obj = cls(**{k: v for k, v in values.items() if k in names})
        obj.validate()
        return obj

    def changed(self, **values):
        obj = replace(self, **values)
        obj.validate()
        return obj

    def validate(self):
        if not isinstance(self.mode, str) or self.mode not in MODES:
            raise ValueError("Unknown modulation")
        for name in ("muted", "agc", "force_mono", "economy", "biastee"):
            if type(getattr(self, name)) is not bool:
                raise ValueError(f"{name} must be true or false")
        if not isinstance(self.audio_device, str) or not self.audio_device or "\0" in self.audio_device:
            raise ValueError("Invalid audio device")
        if not isinstance(self.antenna, str):
            raise ValueError("Invalid antenna")
        for name in ("frequency", "step", "sample_rate", "bandwidth", "gain", "ppm", "squelch", "volume"):
            if not isinstance(getattr(self, name), (int, float)) or not math.isfinite(getattr(self, name)):
                raise ValueError(f"Invalid {name}")
        for name in ("frequency", "step", "sample_rate", "bandwidth"):
            if type(getattr(self, name)) is not int:
                raise ValueError(f"{name} must be a whole number")
        if not 0 <= self.frequency <= 100_000_000_000:
            raise ValueError("Frequency is out of range")
        if not 1 <= self.step <= 100_000_000 or not 240000 <= self.sample_rate <= 20000000:
            raise ValueError("Invalid tuning step or sample rate")
        limits = MODES[self.mode].get("bandwidth_range") or ((80000, 220000) if self.mode == "WFM" else ((500, 6000) if self.mode in ("USB", "LSB") else (2000, 20000)))
        if not limits[0] <= self.bandwidth <= limits[1]:
            raise ValueError(f"{self.mode} bandwidth must be {limits[0]}–{limits[1]} Hz")
        if not 0 <= self.volume <= 1 or not -140 <= self.squelch <= 0 or not -200 <= self.ppm <= 200:
            raise ValueError("Volume, squelch or PPM out of range")
        if not isinstance(self.gain_stages, dict) or any(not isinstance(v, (int, float)) or not math.isfinite(v) for v in self.gain_stages.values()):
            raise ValueError("Invalid gain stages")
        if self.deemphasis not in (50, 75):
            raise ValueError("FM de-emphasis must be 50 or 75 µs")


def parse_frequency(text, unit="MHz"):
    from decimal import Decimal, InvalidOperation
    try:
        value = Decimal(text.strip().replace(" ", "")) * {"Hz": 1, "kHz": 1000, "MHz": 1000000}[unit]
        if not value.is_finite() or value < 0 or value > 100_000_000_000:
            raise ValueError("Frequency out of range")
        if value != value.to_integral_value():
            raise ValueError("Frequency must resolve to whole Hz")
        return int(value)
    except (InvalidOperation, KeyError) as exc:
        raise ValueError("Enter a valid frequency and unit") from exc

# Entry points expose lightweight descriptors; build() imports DSP lazily in the worker.
EXTERNAL_DEMODULATORS = {}
PLUGIN_ERRORS = {}

def _register_demodulators():
    from importlib.metadata import entry_points
    for point in entry_points(group="touchwave.demodulators"):
        try:
            plugin = point.load()()
            ident = plugin.id
            if ident in MODES:
                raise ValueError(f"Duplicate demodulator ID {ident}")
            if not 8000 <= plugin.channel_rate <= 1000000:
                raise ValueError("Invalid demodulator channel rate")
            lo, hi = plugin.bandwidth_range
            if not 0 < lo <= plugin.default_bandwidth <= hi < plugin.channel_rate:
                raise ValueError("Invalid demodulator bandwidth range")
            EXTERNAL_DEMODULATORS[ident] = plugin
            MODES[ident] = dict(label=plugin.label, bandwidth=plugin.default_bandwidth,
                                rate=plugin.channel_rate, bandwidth_range=(lo,hi))
        except Exception as exc:
            PLUGIN_ERRORS[point.name] = str(exc)

_register_demodulators()
