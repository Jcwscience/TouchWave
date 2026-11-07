from kivy.clock import Clock
from kivy.graphics import Color, Ellipse, RoundedRectangle
from kivy.metrics import dp, sp
from kivy.properties import BooleanProperty, StringProperty
from kivy.uix.slider import Slider
from kivy.uix.button import Button
from kivy.uix.label import Label
from kivy.uix.textinput import TextInput
from .theme import THEME


class TouchButton(Button):
    tone = StringProperty("raised")

    def __init__(self, **kwargs):
        kwargs.setdefault("font_size", sp(THEME.body_font))
        super().__init__(background_normal="", background_down="", background_color=(0, 0, 0, 0),
                         color=THEME.color("text"), **kwargs)
        with self.canvas.before:
            self.fill_color = Color(*THEME.color(self.tone))
            self.fill = RoundedRectangle(pos=self.pos, size=self.size, radius=[dp(THEME.radius)])
        self.bind(pos=self._draw, size=self._draw, state=self._draw, tone=self._draw, disabled=self._draw)
        self._draw()

    def _draw(self, *_):
        self.fill.pos, self.fill.size = self.pos, self.size
        token = "border" if self.state == "down" else self.tone
        self.fill_color.rgba = THEME.color(token)
        self.color = THEME.color("background" if self.tone in ("accent", "danger", "warning") else "text")
        self.opacity = 0.4 if self.disabled else 1


class RepeatButton(TouchButton):
    """A large tuning control that also supports hold-to-repeat."""
    def __init__(self, action, **kwargs):
        super().__init__(**kwargs)
        self.action = action
        self.timer = None
        self.bind(on_press=self._press, on_release=self._release)

    def _press(self, *_):
        self.action()
        self.timer = Clock.schedule_once(self._repeat, 0.45)

    def _repeat(self, *_):
        self.action()
        self.timer = Clock.schedule_interval(lambda dt: self.action(), 0.12)

    def _release(self, *_):
        if self.timer:
            self.timer.cancel()


def label(text, size=13, token="text", **kwargs):
    widget = Label(text=text, font_size=sp(size), color=THEME.color(token), **kwargs)
    widget.bind(size=lambda obj, value: setattr(obj, "text_size", value))
    return widget


def entry(text="", hint="", **kwargs):
    return TextInput(text=text, hint_text=hint, multiline=False, font_size=sp(16),
                     foreground_color=THEME.color("text"), background_normal="",
                     background_active="", background_color=THEME.color("raised"),
                     cursor_color=THEME.color("accent"), selection_color=THEME.color("selection"), padding=[dp(12), dp(12)], **kwargs)


class TouchSlider(Slider):
    """Theme-colored track with a full-height native Slider touch target."""
    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.bind(pos=self._draw, size=self._draw, value=self._draw, min=self._draw, max=self._draw)
        self._draw()

    def _draw(self, *_):
        self.canvas.clear()
        x, y = self.value_pos
        with self.canvas:
            Color(*THEME.color("border"))
            RoundedRectangle(pos=(self.x+self.padding, self.center_y-dp(2)),
                             size=(max(0, self.width-2*self.padding), dp(4)), radius=[dp(2)])
            Color(*THEME.color("accent"))
            RoundedRectangle(pos=(self.x+self.padding, self.center_y-dp(2)),
                             size=(max(0,x-self.x-self.padding),dp(4)), radius=[dp(2)])
            Ellipse(pos=(x-dp(8),self.center_y-dp(8)),size=(dp(16),dp(16)))


class TouchSwitch(TouchButton):
    active = BooleanProperty(False)

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.bind(active=self._sync, on_release=self._toggle)
        self._sync()

    def _toggle(self, *_):
        self.active = not self.active

    def _sync(self, *_):
        self.text = "On" if self.active else "Off"
        self.tone = "accent" if self.active else "raised"
