"""GPU spectrum and circular-texture waterfall. Only called from Kivy's thread."""
import time
import numpy as np
from kivy.graphics import Color, Line, Rectangle
from kivy.graphics.texture import Texture
from kivy.metrics import dp
from kivy.uix.widget import Widget
from .theme import THEME


class RadioPlot(Widget):
    def __init__(self, tune, **kwargs):
        super().__init__(**kwargs)
        self.tune = tune
        self.frequency, self.rate, self.bandwidth = 100100000, 2400000, 180000
        self.mode = "WFM"
        self.zoom = 1.0
        self.floor, self.ceiling = -110.0, -10.0
        self.average = 0.55
        self.peak_hold = False
        self.scroll_fps = 20
        self.last_row = 0
        self.spectrum = None
        self.peaks = None
        self.cursor = 0
        self._touches = {}
        self.pinched = False
        self.texture = Texture.create(size=(512, 256), colorfmt="rgb")
        self.texture.wrap = "repeat"
        self.texture.mag_filter = "linear"
        self.texture.min_filter = "linear"
        self.texture.blit_buffer(bytes(512*256*3), colorfmt="rgb", bufferfmt="ubyte")
        self.texture.add_reload_observer(lambda texture: self.clear())
        stops = np.array([[8, 12, 16], [17, 31, 66], [34, 69, 115], [35, 147, 168], [81, 221, 210], [246, 220, 153]], dtype=float)
        self.lut = np.column_stack([np.interp(np.linspace(0, 5, 256), np.arange(6), stops[:, c]) for c in range(3)]).astype(np.uint8)
        self.bind(pos=self.redraw, size=self.redraw)

    @property
    def span(self):
        return self.rate / self.zoom

    def clear(self):
        self.spectrum, self.peaks = None, None
        self.texture.blit_buffer(bytes(512*256*3), colorfmt="rgb", bufferfmt="ubyte")
        self.cursor = 0
        self.redraw()

    def receive(self, frame):
        if self.frequency != frame["frequency"] or self.rate != frame["rate"]:
            self.clear()
        self.frequency, self.rate = frame["frequency"], frame["rate"]
        raw = np.array(frame["db"], dtype=np.float32)
        self.spectrum = raw if self.spectrum is None else self.average * self.spectrum + (1-self.average) * raw
        self.peaks = raw if self.peaks is None else np.maximum(self.peaks, raw)
        now = time.monotonic()
        if now-self.last_row >= 1 / self.scroll_fps:
            values = self.visible(self.spectrum, 512)
            indices = np.clip((values-self.floor)/(self.ceiling-self.floor)*255, 0, 255).astype(np.uint8)
            self.cursor = (self.cursor-1) % 256
            self.texture.blit_buffer(self.lut[indices].tobytes(), size=(512, 1), pos=(0, self.cursor), colorfmt="rgb", bufferfmt="ubyte")
            self.last_row = now
        self.redraw()

    def visible(self, values, count):
        n = len(values)
        half = n / self.zoom / 2
        locations = np.linspace(n/2-half, n/2+half, count, endpoint=False)
        return np.interp(locations, np.arange(n), values)

    def redraw(self, *_):
        self.canvas.clear()
        x, y, w, h = self.x, self.y, self.width, self.height
        split = y + h*0.58
        with self.canvas:
            Color(*THEME.color("background"))
            Rectangle(pos=self.pos, size=self.size)
            Color(1, 1, 1, 1)
            offset = self.cursor / 256
            Rectangle(texture=self.texture, pos=(x, y), size=(w, h*0.58),
                      tex_coords=(0, offset+1, 1, offset+1, 1, offset, 0, offset))
            Color(*THEME.color("border"))
            for i in range(1, 5):
                Line(points=[x+w*i/5, y, x+w*i/5, y+h], width=0.5)
            for i in range(4):
                gy = split+(h*0.42)*i/3
                Line(points=[x, gy, x+w, gy], width=0.5)
            if self.spectrum is not None:
                count = min(640, max(64, int(w)))
                values = self.visible(self.spectrum, count)
                coords = np.empty(count*2)
                coords[::2] = np.linspace(x, x+w, count)
                coords[1::2] = split+np.clip((values-self.floor)/(self.ceiling-self.floor), 0, 1)*h*0.42
                Color(*THEME.color("accent"))
                Line(points=coords.tolist(), width=1.05)
                if self.peak_hold:
                    peak = self.visible(self.peaks, count)
                    coords[1::2] = split+np.clip((peak-self.floor)/(self.ceiling-self.floor), 0, 1)*h*0.42
                    Color(0.96, 0.78, 0.47, 0.65)
                    Line(points=coords.tolist(), width=0.7)
            left, right = -self.bandwidth/2, self.bandwidth/2
            if self.mode == "USB":
                left, right = 0, self.bandwidth
            elif self.mode == "LSB":
                left, right = -self.bandwidth, 0
            a, b = x+w*(0.5+left/self.span), x+w*(0.5+right/self.span)
            a, b = max(x, a), min(x+w, b)
            Color(0.32, 0.87, 0.82, 0.10)
            Rectangle(pos=(a, y), size=(max(0, b-a), h))
            Color(0.32, 0.87, 0.82, 0.7)
            Line(points=[x+w/2, y, x+w/2, y+h], width=0.7)

    def on_touch_down(self, touch):
        if not self.collide_point(*touch.pos):
            return super().on_touch_down(touch)
        if touch.is_mouse_scrolling:
            self.zoom = float(np.clip(self.zoom*(1.5 if touch.button == "scrollup" else 1/1.5), 1, 128))
            self.redraw()
            return True
        if touch.is_double_tap:
            self.zoom = 1
            self.redraw()
            return True
        self._touches[touch.uid] = dict(start=touch.pos, current=touch.pos)
        if len(self._touches) == 2:
            self.pinched = True
            points = [v["current"] for v in self._touches.values()]
            self.pinch_start = max(1, abs(points[0][0]-points[1][0]))
            self.pinch_zoom = self.zoom
        touch.grab(self)
        return True

    def on_touch_move(self, touch):
        if touch.grab_current is self:
            self._touches[touch.uid]["current"] = touch.pos
            if len(self._touches) == 2:
                points = [v["current"] for v in self._touches.values()]
                distance = max(1, abs(points[0][0]-points[1][0]))
                self.zoom = float(np.clip(self.pinch_zoom * distance / self.pinch_start, 1, 128))
                self.redraw()
            return True
        return super().on_touch_move(touch)

    def on_touch_up(self, touch):
        if touch.grab_current is self:
            start = self._touches.pop(touch.uid)["start"]
            touch.ungrab(self)
            if not self.pinched:
                dx = touch.x-start[0]
                offset = -dx/self.width*self.span if abs(dx) > dp(12) else ((touch.x-self.x)/self.width-0.5)*self.span
                self.tune(round(self.frequency+offset))
            if not self._touches:
                self.pinched = False
            return True
        return super().on_touch_up(touch)
