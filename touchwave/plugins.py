"""Small extension contracts. Plugins execute locally and must be trusted."""
from dataclasses import dataclass, field
from importlib.metadata import entry_points
from typing import Protocol, Any
from .model import ReceiverConfig


class DeviceProvider(Protocol):
    def discover(self) -> list[dict]: ...
    def capabilities(self, selection: dict) -> dict: ...


class ReceiverEngine(Protocol):
    def start(self, selection: dict, config: ReceiverConfig): ...
    def stop(self): ...
    def configure(self, config: ReceiverConfig): ...


class DemodulatorPlugin(Protocol):
    id: str
    label: str
    channel_rate: int
    default_bandwidth: int
    bandwidth_range: tuple[int, int]
    parameters: dict
    def build(self, config: ReceiverConfig) -> Any:
        """Return a GNU Radio block with complex input and two float outputs."""
        ...


class DecoderPlugin(Protocol):
    id: str
    label: str
    def build(self, selection: dict, config: ReceiverConfig, emit) -> Any:
        """Return a top_block; publish DecodedEvent objects through emit()."""
        ...


class ToolPlugin(Protocol):
    id: str
    label: str
    requires_device: bool
    decoder_id: str | None
    def create_view(self, application) -> Any: ...


@dataclass(frozen=True)
class DecodedEvent:
    source: str
    kind: str
    timestamp: float
    payload: dict = field(default_factory=dict)
    version: int = 1


def load_plugins(group):
    loaded, errors = {}, {}
    for item in entry_points(group=f"touchwave.{group}"):
        try:
            loaded[item.name] = item.load()()
        except Exception as exc:
            errors[item.name] = str(exc)
    return loaded, errors


class ResourceManager:
    """A single explicit lease; future tools use the same ownership rule."""
    def __init__(self):
        self.owner = None

    def acquire(self, owner):
        if self.owner not in (None, owner):
            raise RuntimeError(f"Radio is in use by {self.owner}")
        self.owner = owner

    def release(self, owner):
        if self.owner == owner:
            self.owner = None
