"""Semantic design tokens; widgets never choose their own palette."""
from dataclasses import dataclass
from kivy.utils import get_color_from_hex


@dataclass(frozen=True)
class Theme:
    background: str = "#080C10"
    surface: str = "#101820"
    raised: str = "#18232D"
    border: str = "#263540"
    text: str = "#EAF2F6"
    secondary: str = "#8CA0AF"
    accent: str = "#51DDD2"
    selection: str = "#51DDD240"
    danger: str = "#FF7786"
    warning: str = "#F6C777"

    body_font: float = 16
    secondary_font: float = 12
    title_font: float = 19
    frequency_font: float = 24
    radius: float = 9
    gap: float = 4
    padding: float = 8

    def color(self, token):
        return get_color_from_hex(getattr(self, token))


THEME = Theme()
