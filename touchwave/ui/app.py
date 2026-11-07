"""Portrait handheld UI. Application commands are the only hardware boundary."""
from pathlib import Path
import subprocess
import time
from kivy.app import App
from kivy.clock import Clock
from kivy.core.window import Window
from kivy.metrics import Metrics, dp, sp
from kivy.lang import Builder
from kivy.uix.boxlayout import BoxLayout
from kivy.uix.gridlayout import GridLayout
from kivy.uix.modalview import ModalView
from kivy.uix.scrollview import ScrollView
from kivy.uix.spinner import Spinner
from .theme import THEME
from .widgets import TouchButton, RepeatButton, TouchSlider, TouchSwitch, label, entry
from .plots import RadioPlot
from ..client import WorkerClient
from ..model import MODES, ReceiverConfig, parse_frequency
from ..plugins import load_plugins
from ..scanner import Scanner
from ..storage import Store


class TouchWaveApp(App):
    title = "TouchWave"

    def __init__(self, demo=False, no_audio=False, fullscreen=True, data_path=None, **kwargs):
        super().__init__(**kwargs)
        self.demo, self.no_audio, self.fullscreen = demo, no_audio, fullscreen
        self.store = Store(data_path)
        self.config_state = self.store.load_config()
        self.scanner = Scanner()
        self.client = None
        self.state = {}
        self.devices = []
        self.phase = "idle"
        self.initializing = True
        self.indexed = set()
        self.configure_event = None
        self.pending_config = None
        self.last_heartbeat = time.monotonic()
        self.modal = None
        self.player = None
        self.event_handlers = []
        self.last_event_sequence = 0
        self.stopped = False
        self.saved_config = self.config_state
        self.tools, self.plugin_errors = load_plugins("tools")

    def build(self):
        Window.clearcolor = THEME.color("background")
        Builder.load_string(f"""<VKeyboard>:
    background_color: {THEME.color('surface')}
    key_background_color: {THEME.color('border')}
""")
        if self.fullscreen:
            Window.fullscreen = "auto"
        else:
            Window.size = (540, 620)
        # 480 logical units across the 65 mm panel: a 60-unit control is ~8 mm.
        Metrics.density = Window.width / 480
        root = BoxLayout(orientation="vertical", padding=dp(THEME.padding), spacing=dp(THEME.gap))
        header = BoxLayout(size_hint_y=None, height=dp(22))
        header.add_widget(label("TOUCHWAVE", size=12, token="accent", halign="left", bold=True, size_hint_x=0.38))
        self.hardware_label = label("CONNECTING", size=10, token="secondary", halign="right")
        header.add_widget(self.hardware_label)
        root.add_widget(header)
        tune = BoxLayout(size_hint_y=None, height=dp(64), spacing=dp(5))
        tune.add_widget(RepeatButton(text="−", font_size=sp(26), size_hint_x=None, width=dp(58), action=lambda: self.step(-1)))
        self.frequency_button = TouchButton(text="", tone="surface", markup=True)
        self.frequency_button.bind(on_release=lambda *_: self.frequency_dialog())
        tune.add_widget(self.frequency_button)
        tune.add_widget(RepeatButton(text="+", font_size=sp(26), size_hint_x=None, width=dp(58), action=lambda: self.step(1)))
        root.add_widget(tune)
        modes = BoxLayout(size_hint_y=None, height=dp(36), spacing=dp(4))
        self.mode_buttons = {}
        for mode in MODES:
            button = TouchButton(text="FM stereo" if mode == "WFM" else mode, font_size=sp(14))
            button.bind(on_release=lambda _, m=mode: self.set_mode(m))
            modes.add_widget(button)
            self.mode_buttons[mode] = button
        root.add_widget(modes)
        plots = BoxLayout(orientation="vertical", spacing=dp(2))
        self.plot_header = label("SPECTRUM  /  dBFS", size=10, token="secondary", halign="left", size_hint_y=None, height=dp(18))
        plots.add_widget(self.plot_header)
        self.plot = RadioPlot(tune=self.tune_frequency)
        plots.add_widget(self.plot)
        axis = BoxLayout(size_hint_y=None, height=dp(18))
        self.axis_labels = [label("", size=10, token="secondary", halign=align) for align in ("left", "center", "right")]
        for item in self.axis_labels:
            axis.add_widget(item)
        plots.add_widget(axis)
        root.add_widget(plots)
        actions = BoxLayout(size_hint_y=None, height=dp(58), spacing=dp(5))
        self.rx_button = TouchButton(text="Start RX", tone="accent")
        self.rx_button.bind(on_release=lambda *_: self.toggle_rx())
        actions.add_widget(self.rx_button)
        self.mute_button = TouchButton(text="Mute")
        self.mute_button.bind(on_release=lambda *_: self.update_config(muted=not self.config_state.muted))
        actions.add_widget(self.mute_button)
        self.record_button = TouchButton(text="Record")
        self.record_button.bind(on_release=lambda *_: self.record_dialog())
        actions.add_widget(self.record_button)
        button = TouchButton(text="Bookmark +")
        button.bind(on_release=lambda *_: self.bookmark_dialog())
        actions.add_widget(button)
        root.add_widget(actions)
        nav = BoxLayout(size_hint_y=None, height=dp(50), spacing=dp(5))
        for text, action in [("Library", self.library_dialog), ("Scan", self.scan_dialog),
                             ("Controls", self.settings_dialog), ("View", self.view_dialog)]:
            button = TouchButton(text=text, tone="surface")
            button.bind(on_release=lambda _, fn=action: fn())
            nav.add_widget(button)
        root.add_widget(nav)
        self.notice_label = label("Ready · reception stopped", size=10, token="secondary", halign="left", size_hint_y=None, height=dp(20))
        root.add_widget(self.notice_label)
        Window.bind(on_key_down=self.key_down)
        self.refresh_config()
        self.start_worker()
        Clock.schedule_interval(self.poll, 1/30)
        return root

    def start_worker(self):
        self.initializing = True
        self.rx_button.disabled = True
        if self.configure_event:
            self.configure_event.cancel()
        self.pending_config = None
        if self.client:
            self.client.close()
            self.store.recover_recordings()
        self.saved_config = self.config_state
        self.restore_preferences = True
        self.client = WorkerClient(self.store.recordings, self.no_audio)
        self.last_heartbeat = time.monotonic()
        self.last_event_sequence = 0
        self.state = {}
        if self.demo:
            from ..devices import SIMULATOR
            self.devices = [SIMULATOR]
            self.client.send("select", self.selected, device=SIMULATOR)
        else:
            self.client.send("discover", self.discovered)

    def discovered(self, reply):
        if not reply["ok"]:
            return
        self.devices = reply["result"]
        selected = next((d for d in self.devices if d["driver"] == "demo"), None) if self.demo else next(iter(self.devices), None)
        if selected:
            self.client.send("select", self.selected, device=selected)

    def selected(self, reply):
        if reply["ok"]:
            self.initializing = False
            # Preserve device-negotiated rate/gain, plus the saved listening preferences.
            self.apply_state(reply["state"])
            if self.restore_preferences:
                self.restore_preferences = False
                from ..devices import restore_compatible_config
                saved = restore_compatible_config(self.saved_config, self.config_state,
                                                  self.state["capabilities"]).to_dict()
                if saved["audio_device"] == "default":
                    cards = Path("/proc/asound/cards")
                    if cards.exists() and "USB Audio" in cards.read_text():
                        import re
                        match = re.search(r"\[([^\]]+)\].*USB-Audio", cards.read_text())
                        if match:
                            saved["audio_device"] = f"plughw:CARD={match[1].strip()},DEV=0"
                self.update_config(**saved)
            self.notify("Ready · tap Start RX")

    def apply_state(self, state):
        if state.get("state_time", 0) < self.state.get("state_time", 0):
            return
        previous_phase = self.phase
        previous_error = self.state.get("error")
        self.state = state
        if state.get("error") and state["error"] != previous_error:
            self.notify(state["error"], error=True)
        self.last_heartbeat = time.monotonic()
        self.phase = state["phase"]
        if self.phase != previous_phase and not state.get("error"):
            self.notify("Receiving" if self.phase == "receiving" else "Reception stopped")
        if not self.pending_config:
            self.config_state = ReceiverConfig.from_dict(state["config"])
            self.refresh_config()
        for item in state.get("decoded_events", []):
            if item["sequence"] > self.last_event_sequence:
                self.last_event_sequence = item["sequence"]
                for handler in tuple(self.event_handlers):
                    try:
                        handler(item["event"])
                    except Exception as exc:
                        self.notify(f"Tool event handler failed: {exc}", error=True)
        device = state.get("device", {})
        driver = "IQ REPLAY" if state.get("replay") else device.get("driver", "").upper()
        temp = state.get("telemetry", {}).get("temperature")
        self.hardware_label.text = f"{driver}  ·  {self.phase.upper()}" + (f"  ·  {temp:.0f}°C" if temp else "")
        self.rx_button.disabled = self.initializing or self.phase in ("starting", "stopping")
        self.rx_button.text = "Stop RX" if self.phase == "receiving" else "Start RX"
        self.rx_button.tone = "raised" if self.phase == "receiving" else "accent"
        recording = state.get("recording")
        self.record_button.text = f"Stop {recording['seconds']:.0f}s" if recording else "Record"
        self.record_button.tone = "danger" if recording else "raised"
        level = state.get("level", -140)
        pilot = "STEREO" if state.get("pilot") and not self.config_state.force_mono else "MONO"
        self.plot_header.text = f"SPECTRUM  ·  {level:.0f} dBFS  ·  {pilot if self.config_state.mode == 'WFM' else self.config_state.mode}     {self.plot.ceiling:.0f} / {self.plot.floor:.0f} dBFS"
        for metadata in state.get("completed", []):
            if metadata["path"] not in self.indexed:
                self.store.index_recording(metadata)
                self.indexed.add(metadata["path"])
                self.notify("Recording saved · " + metadata["reason"])
        if state.get("error"):
            self.scanner.stop()

    def poll(self, dt):
        messages, replies = self.client.drain()
        for callback, reply in replies:
            if "state" in reply:
                self.apply_state(reply["state"])
            if not reply.get("ok"):
                self.pending_config = None
                self.notify(reply.get("error", "Receiver error"), error=True)
                if "state" in reply:
                    self.config_state = ReceiverConfig.from_dict(reply["state"]["config"])
                    self.refresh_config()
            if callback:
                callback(reply)
        if "state" in messages:
            self.apply_state(messages["state"])
        if "spectrum" in messages:
            self.plot.receive(messages["spectrum"])
        span = self.plot.span / 1e6
        freq = self.plot.frequency / 1e6
        for item, value in zip(self.axis_labels, (freq-span/2, freq, freq+span/2)):
            item.text = f"{value:.3f} MHz"
        if self.player and self.player.poll() is not None:
            _, error = self.player.communicate()
            if self.player.returncode:
                self.notify("Audio playback failed: " + error.decode(errors="replace")[:120], error=True)
            self.player = None
        if self.scanner.active and self.phase == "receiving" and not self.pending_config:
            row = self.scanner.tick(time.monotonic(), self.state.get("level", -140), self.config_state.squelch)
            if row:
                self.load_bookmark(row, scanning=True)
                self.notify("Scanning · " + row["name"])
        if self.client.process.poll() is not None or time.monotonic()-self.last_heartbeat > 20:
            self.scanner.stop()
            self.phase = "error"
            self.rx_button.text = "Worker offline"
            self.rx_button.disabled = True
            self.notify("Worker offline · restart in Controls", error=True)

    def refresh_config(self):
        c = self.config_state
        self.frequency_button.text = f"[b]{c.frequency / 1e6:,.6f}[/b] MHz\n[size={sp(12):.0f}]STEP {c.step/1000:g} kHz   ·   FILTER {c.bandwidth/1000:g} kHz[/size]"
        self.frequency_button.font_size = sp(THEME.frequency_font)
        self.mute_button.text = "Unmute" if c.muted else "Mute"
        for mode, button in self.mode_buttons.items():
            button.tone = "accent" if mode == c.mode else "surface"
        self.plot.bandwidth, self.plot.mode = c.bandwidth, c.mode
        if self.phase != "receiving":
            self.plot.frequency = c.frequency
        self.plot.redraw()

    def update_config(self, **changes):
        try:
            self.config_state = self.config_state.changed(**changes)
            self.pending_config = self.config_state.to_dict()
            self.refresh_config()
            if self.configure_event:
                self.configure_event.cancel()
            self.configure_event = Clock.schedule_once(self.send_config, 0.18)
        except (ValueError, TypeError) as exc:
            self.notify(str(exc), error=True)

    def send_config(self, *_):
        config = self.pending_config
        if config:
            def done(reply):
                if self.pending_config == config:
                    self.pending_config = None
                if reply.get("ok"):
                    self.apply_state(reply["state"])
                    self.store.save_config(self.config_state)
                    if self.scanner.active:
                        now = time.monotonic()
                        self.scanner.deadline = now + self.scanner.dwell
                        self.scanner.settle_until = now + 0.25
            self.client.send("configure", done, config=config)

    def tune_frequency(self, frequency):
        self.scanner.stop()
        if self.state.get("recording"):
            self.notify("Stop recording before tuning", error=True)
            return
        self.update_config(frequency=frequency)

    def step(self, direction):
        self.tune_frequency(self.config_state.frequency + direction*self.config_state.step)

    def set_mode(self, mode):
        self.scanner.stop()
        self.update_config(mode=mode, bandwidth=MODES[mode]["bandwidth"])

    def toggle_rx(self):
        self.scanner.stop()
        if self.pending_config:
            if self.configure_event:
                self.configure_event.cancel()
            self.send_config()
        self.stop_player()
        self.client.send("stop" if self.phase == "receiving" else "start")

    def notify(self, message, error=False):
        self.notice_label.text = message
        self.notice_label.color = THEME.color("danger" if error else "secondary")
        if self.modal and self.modal.parent and hasattr(self.modal, "notice"):
            self.modal.notice.text = message
            self.modal.notice.color = THEME.color("danger" if error else "secondary")

    def sheet(self, title):
        if self.modal:
            self.modal.dismiss()
        modal = ModalView(size_hint=(0.96, 0.94), background="", background_color=THEME.color("background"))
        layout = BoxLayout(orientation="vertical", padding=dp(12), spacing=dp(8))
        top = BoxLayout(size_hint_y=None, height=dp(40), spacing=dp(8))
        top.add_widget(label(title, size=THEME.title_font, halign="left", bold=True))
        close = TouchButton(text="Done", size_hint_x=None, width=dp(70))
        close.bind(on_release=lambda *_: modal.dismiss())
        top.add_widget(close)
        layout.add_widget(top)
        modal.header = top
        scroll = ScrollView(do_scroll_x=False)
        body = BoxLayout(orientation="vertical", size_hint_y=None, spacing=dp(8))
        body.bind(minimum_height=body.setter("height"))
        scroll.add_widget(body)
        layout.add_widget(scroll)
        modal.notice = label("", size=11, token="secondary", halign="left", size_hint_y=None, height=dp(28))
        layout.add_widget(modal.notice)
        modal.add_widget(layout)
        modal.bind(on_dismiss=lambda *_: Window.release_all_keyboards())
        modal.open()
        self.modal = modal
        return modal, body

    def line(self, body, text, height=26):
        obj = label(text, size=12, token="secondary", halign="left", size_hint_y=None, height=dp(height))
        body.add_widget(obj)
        return obj

    def action(self, body, text, callback, tone="raised"):
        button = TouchButton(text=text, tone=tone, size_hint_y=None, height=dp(46))
        button.bind(on_release=lambda *_: callback())
        body.add_widget(button)
        return button

    def frequency_dialog(self):
        modal, body = self.sheet("Tune frequency")
        field = entry(f"{self.config_state.frequency/1e6:.6f}", keyboard_mode="managed", size_hint_y=None, height=dp(56))
        input_row = BoxLayout(size_hint_y=None, height=dp(56), spacing=dp(6))
        input_row.add_widget(field)
        clear = TouchButton(text="Clear", size_hint_x=None, width=dp(70))
        clear.bind(on_release=lambda *_: setattr(field, "text", ""))
        input_row.add_widget(clear)
        body.add_widget(input_row)
        units = Spinner(text="MHz", values=("MHz", "kHz", "Hz"), size_hint_y=None, height=dp(44),
                        background_normal="", background_color=THEME.color("raised"), font_size=sp(16))
        body.add_widget(units)
        keypad = GridLayout(cols=3, spacing=dp(6), size_hint_y=None, height=dp(232))
        for key in ("1", "2", "3", "4", "5", "6", "7", "8", "9", ".", "0", "DEL"):
            button = TouchButton(text=key, font_size=sp(24))
            def press(_, k=key):
                self.edit_frequency(field, k)
            button.bind(on_release=press)
            keypad.add_widget(button)
        body.add_widget(keypad)
        def tune():
            try:
                frequency = parse_frequency(field.text, units.text)
                self.tune_frequency(frequency)
                modal.dismiss()
            except ValueError as exc:
                self.notify(str(exc), error=True)
        self.tuning_modal = modal
        self.frequency_entry = field
        self.frequency_submit = tune
        self.action(body, "Tune", tune, "accent")
        Clock.schedule_once(lambda dt: field.select_all(), 0.2)

    @staticmethod
    def edit_frequency(field, key):
        if field.selection_text:
            field.delete_selection()
            if key == "DEL":
                return
        if key == "DEL":
            field.do_backspace()
        else:
            field.insert_text(key)

    def bookmark_dialog(self):
        modal, body = self.sheet("Save bookmark")
        name = entry(f"{self.config_state.frequency/1e6:.4f} MHz", size_hint_y=None, height=dp(50))
        tags = entry(hint="Tags, e.g. local, aviation", size_hint_y=None, height=dp(50))
        body.add_widget(name)
        body.add_widget(tags)
        def save():
            try:
                self.store.add_bookmark(name.text, tags.text, self.config_state)
                modal.dismiss()
                self.notify("Bookmark saved")
            except ValueError as exc:
                self.notify(str(exc), error=True)
        self.action(body, "Save current frequency", save, "accent")

    def load_bookmark(self, row, scanning=False):
        if not scanning:
            self.scanner.stop()
        saved = row["config"]
        self.update_config(**{k: saved[k] for k in ("frequency", "mode", "bandwidth", "step")})
        if not scanning:
            self.notify("Tuned · " + row["name"])

    def library_dialog(self):
        _, body = self.sheet("Library")
        search = entry(hint="Search bookmarks or tags", size_hint_y=None, height=dp(48))
        body.add_widget(search)
        rows = BoxLayout(orientation="vertical", size_hint_y=None, spacing=dp(6))
        rows.bind(minimum_height=rows.setter("height"))
        body.add_widget(rows)
        def populate(*_):
            rows.clear_widgets()
            for item in self.store.bookmarks(search.text):
                row = BoxLayout(size_hint_y=None, height=dp(54), spacing=dp(5))
                button = TouchButton(text=f"{item['name']}  ·  {item['config']['mode']}\n{item['config']['frequency']/1e6:.6f} MHz  {item['tags']}", font_size=sp(12))
                button.bind(on_release=lambda _, r=item: self.load_bookmark(r))
                row.add_widget(button)
                delete = TouchButton(text="Delete", size_hint_x=None, width=dp(64), font_size=sp(11))
                delete.bind(on_release=lambda _, r=item: self.delete_bookmark_dialog(r))
                row.add_widget(delete)
                rows.add_widget(row)
            if not rows.children:
                self.line(rows, "No bookmarks yet · save one from the receiver")
        search.bind(text=populate)
        populate()
        self.action(body, "Import / export bookmarks", self.transfer_dialog)
        self.line(body, "RECORDINGS")
        for recording in self.store.list_recordings():
            name = Path(recording["path"]).name
            self.action(body, f"{name}\n{recording['seconds']:.1f}s · {recording['reason']}",
                        lambda r=recording: self.play_recording(r))
        if not self.store.list_recordings():
            self.line(body, "No recordings yet")
        if self.tools:
            self.line(body, "TOOLS")
            for plugin in self.tools.values():
                self.action(body, plugin.label, lambda p=plugin: self.open_tool(p))

    def open_tool(self, plugin):
        try:
            decoder_id = getattr(plugin, "decoder_id", None)
            if plugin.requires_device and not decoder_id:
                raise ValueError("Tool must name its worker decoder")
            view = plugin.create_view(self)
            modal, body = self.sheet(plugin.label)
            body.add_widget(view)
            if plugin.requires_device:
                self.scanner.stop()
                self.client.send("tool_start", id=decoder_id)
            def dispose(*_):
                if plugin.requires_device:
                    self.client.send("stop")
                if hasattr(view, "dispose"):
                    view.dispose()
            modal.bind(on_dismiss=dispose)
        except Exception as exc:
            self.notify(f"Tool failed: {exc}", error=True)

    def delete_bookmark_dialog(self, row):
        modal, body = self.sheet("Delete bookmark?")
        self.line(body, row["name"])
        def remove():
            self.store.delete_bookmark(row["id"])
            modal.dismiss()
            self.library_dialog()
        self.action(body, "Delete bookmark", remove, "danger")

    def transfer_dialog(self):
        _, body = self.sheet("Bookmark files")
        path = entry(str(self.store.root / "bookmarks.json"), size_hint_y=None, height=dp(52))
        body.add_widget(path)
        def transfer(importing):
            try:
                if importing:
                    count = self.store.import_bookmarks(path.text)
                    self.notify(f"Imported {count} bookmarks")
                else:
                    self.store.export_bookmarks(path.text)
                    self.notify("Bookmarks exported")
            except (OSError, ValueError, KeyError, TypeError) as exc:
                self.notify(str(exc), error=True)
        self.action(body, "Export to file", lambda: transfer(False))
        self.action(body, "Import from file", lambda: transfer(True))

    def slider_row(self, body, title, value, minimum, maximum, step=1):
        caption = self.line(body, f"{title}  {value:g}")
        slider = TouchSlider(min=minimum, max=maximum, value=value, step=step,
                        size_hint_y=None, height=dp(42), cursor_size=(dp(28), dp(28)))
        slider.bind(value=lambda _, v: setattr(caption, "text", f"{title}  {v:g}"))
        body.add_widget(slider)
        return slider

    def switch_row(self, body, title, active):
        row = BoxLayout(size_hint_y=None, height=dp(42))
        row.add_widget(label(title, size=13, halign="left"))
        switch = TouchSwitch(active=active, size_hint_x=None, width=dp(72))
        row.add_widget(switch)
        body.add_widget(row)
        return switch

    def settings_dialog(self):
        modal, body = self.sheet("Receiver controls")
        c = self.config_state
        caps = self.state.get("capabilities", {})
        volume = self.slider_row(body, "Volume", c.volume, 0, 1, 0.01)
        squelch = self.slider_row(body, "Squelch · dBFS", c.squelch, -140, 0)
        gains = caps.get("gain_range", [0, 50])
        gain = self.slider_row(body, "RF gain · dB", c.gain, *gains, step=0.1)
        stage_sliders = {}
        use_stages = None
        if len(caps.get("gains", {})) > 1:
            use_stages = self.switch_row(body, "Use individual gain stages", bool(c.gain_stages))
            for name, (lo, hi) in caps["gains"].items():
                stage_sliders[name] = self.slider_row(body, name + " · dB", c.gain_stages.get(name, lo), lo, hi, 1)
        antenna = None
        if caps.get("antennas"):
            self.line(body, "Antenna input")
            antenna = Spinner(text=c.antenna or caps["antennas"][0], values=caps["antennas"], size_hint_y=None, height=dp(42))
            body.add_widget(antenna)
        bias = None
        if any(s["key"] in ("biastee", "bias_tx") for s in caps.get("settings", [])):
            bias = self.switch_row(body, "Bias tee · DC power on antenna", c.biastee)
        ppm = self.slider_row(body, "Frequency correction · ppm", c.ppm, -100, 100, step=0.1) if caps.get("ppm") else None
        bandwidth = entry(str(c.bandwidth), hint="Bandwidth in Hz", size_hint_y=None, height=dp(46))
        self.line(body, "Channel bandwidth · Hz")
        body.add_widget(bandwidth)
        step_field = entry(str(c.step), hint="Tuning step in Hz", size_hint_y=None, height=dp(46))
        self.line(body, "Tuning step · Hz")
        body.add_widget(step_field)
        agc = self.switch_row(body, "Audio AGC · AM / SSB", c.agc)
        mono = self.switch_row(body, "Force broadcast FM mono", c.force_mono)
        self.line(body, "FM de-emphasis · microseconds")
        deemphasis = Spinner(text=str(c.deemphasis), values=("50", "75"), size_hint_y=None, height=dp(42))
        body.add_widget(deemphasis)
        self.line(body, "Audio output · ALSA name (e.g. plughw:CARD=Device,DEV=0)", height=34)
        audio = entry(c.audio_device, size_hint_y=None, height=dp(46))
        body.add_widget(audio)
        self.line(body, "Hardware sample rate · samples/sec")
        rates = [str(int(r)) for r in caps.get("sample_rates", [])]
        rate = Spinner(text=str(c.sample_rate), values=rates, size_hint_y=None, height=dp(42))
        body.add_widget(rate)
        economy = self.switch_row(body, "Economy · reduce display updates", c.economy)
        def apply():
            try:
                changes = dict(volume=volume.value, squelch=squelch.value, gain=gain.value,
                               bandwidth=int(bandwidth.text), step=int(step_field.text),
                               agc=agc.active, force_mono=mono.active, deemphasis=int(deemphasis.text),
                               audio_device=audio.text, sample_rate=int(rate.text), economy=economy.active)
                changes.update(gain_stages={name: slider.value for name, slider in stage_sliders.items()} if use_stages and use_stages.active else {},
                               antenna=antenna.text if antenna else "", biastee=bias.active if bias else False)
                if ppm:
                    changes["ppm"] = ppm.value
                self.config_state.changed(**changes)
                self.update_config(**changes)
                modal.dismiss()
            except ValueError as exc:
                self.notify(str(exc), error=True)
        self.action(body, "Apply controls", apply, "accent")
        apply_top = TouchButton(text="Apply", tone="accent", size_hint_x=None, width=dp(70))
        apply_top.bind(on_release=lambda *_: apply())
        modal.header.add_widget(apply_top, index=1)
        self.line(body, "DEVICE")
        for device in self.devices:
            self.action(body, device.get("label", device["driver"]), lambda d=device: self.select_device(d))
        self.action(body, "Refresh connected devices", lambda: self.client.send("discover", self.refresh_devices))
        if self.state.get("replay"):
            self.action(body, "Return to live receiver", lambda: self.client.send("live"))
        self.action(body, "Restart receiver worker", self.start_worker)
        self.line(body, f"Worker log: {self.client.log_path}", height=42)
        self.line(body, "Bias power defaults OFF. Hardware overrun reporting varies by driver.", height=36)
        self.action(body, "Quit TouchWave", self.stop, "surface")

    def select_device(self, device):
        self.scanner.stop()
        self.client.send("select", self.selected, device=device)
        if self.modal:
            self.modal.dismiss()

    def refresh_devices(self, reply):
        if reply["ok"]:
            self.devices = reply["result"]
            self.settings_dialog()

    def view_dialog(self):
        modal, body = self.sheet("Spectrum & waterfall")
        zoom = self.slider_row(body, "Zoom", self.plot.zoom, 1, 128)
        floor = self.slider_row(body, "Display floor · dBFS", self.plot.floor, -150, -30)
        ceiling = self.slider_row(body, "Display ceiling · dBFS", self.plot.ceiling, -60, 10)
        average = self.slider_row(body, "Spectrum smoothing", self.plot.average, 0, 0.95, 0.05)
        speed = self.slider_row(body, "Waterfall rows / second", self.plot.scroll_fps, 2, 20)
        peak = self.switch_row(body, "Peak hold", self.plot.peak_hold)
        def apply():
            if floor.value >= ceiling.value:
                self.notify("Display floor must be below ceiling", error=True)
                return
            self.plot.zoom, self.plot.floor, self.plot.ceiling = zoom.value, floor.value, ceiling.value
            self.plot.average, self.plot.scroll_fps, self.plot.peak_hold = average.value, speed.value, peak.active
            self.plot.clear()
            modal.dismiss()
        self.action(body, "Apply view", apply, "accent")
        self.action(body, "Reset zoom and peak history", lambda: (setattr(self.plot, "zoom", 1), self.plot.clear()))
        self.line(body, "Tap: tune · drag: pan · pinch: zoom · double-tap: recenter", height=40)

    def record_dialog(self):
        if self.state.get("recording"):
            self.client.send("record_stop")
            return
        modal, body = self.sheet("Bounded recording")
        self.line(body, "Recording stops at the first time, size or free-space limit.", height=38)
        duration = entry("60", hint="Seconds", size_hint_y=None, height=dp(48))
        size = entry("256", hint="MiB", size_hint_y=None, height=dp(48))
        self.line(body, "Maximum duration · seconds (1–3600)")
        body.add_widget(duration)
        self.line(body, "Maximum file size · MiB (1–2048)")
        body.add_widget(size)
        def record(kind):
            try:
                seconds, limit = float(duration.text), int(size.text)*1024**2
                if not 1 <= seconds <= 3600 or not 1 <= int(size.text) <= 2048:
                    raise ValueError("Recording limits are out of range")
                self.scanner.stop()
                self.client.send("record", kind=kind, duration=seconds, max_bytes=limit)
                modal.dismiss()
            except ValueError as exc:
                self.notify(str(exc), error=True)
        self.action(body, "Record audio · stereo WAV", lambda: record("audio"), "accent")
        self.action(body, "Record IQ · SigMF", lambda: record("iq"))
        self.line(body, "IQ is cf32: 19.5 MiB/s at 2.56 MS/s. Audio records before volume/mute.", height=40)

    def play_recording(self, recording):
        if recording["kind"] == "iq":
            self.scanner.stop()
            self.client.send("replay", path=recording["path"])
            self.modal.dismiss()
        else:
            modal, body = self.sheet("Audio playback")
            self.line(body, Path(recording["path"]).name, height=40)
            self.line(body, "Live reception stops before playback.", height=36)
            def play():
                def stopped(reply):
                    if not reply["ok"]:
                        return
                    if self.player and self.player.poll() is None:
                        self.player.terminate()
                    self.player = subprocess.Popen(["aplay", "-q", "-D", self.config_state.audio_device, recording["path"]],
                                                   stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
                    self.notify("Playing audio recording")
                self.scanner.stop()
                self.client.send("stop", stopped)
            self.action(body, "Play WAV", play, "accent")
            self.action(body, "Stop playback", self.stop_player)

    def stop_player(self):
        if self.player and self.player.poll() is None:
            self.player.terminate()
            self.player.wait(timeout=2)
        self.player = None

    def scan_dialog(self):
        if self.scanner.active:
            self.scanner.stop()
            self.notify("Scan stopped")
            return
        modal, body = self.sheet("Scan bookmarks")
        group = entry(hint="Tag or search text; blank scans all", size_hint_y=None, height=dp(48))
        body.add_widget(group)
        dwell = self.slider_row(body, "Dwell · seconds", 1, 0.3, 5, 0.1)
        hold = self.slider_row(body, "Hold after activity · seconds", 2, 0, 10, 0.5)
        self.line(body, "Set squelch above the noise floor. Manual tuning stops scanning.", height=40)
        def start():
            try:
                if self.state.get("recording") or self.state.get("replay"):
                    raise ValueError("Scanning requires live reception without recording")
                if self.phase != "receiving":
                    raise ValueError("Start RX before scanning")
                items = self.store.bookmarks(group.text)
                caps = self.state.get("capabilities", {})
                ranges = caps.get("frequency_ranges", [[0, 6e9]])
                items = [r for r in items if any(a <= r["config"]["frequency"] <= b for a, b in ranges)]
                self.scanner.start(items, time.monotonic(), dwell.value, hold.value)
                modal.dismiss()
            except ValueError as exc:
                self.notify(str(exc), error=True)
        self.action(body, "Start scanning", start, "accent")

    def key_down(self, window, key, scancode, codepoint, modifiers):
        if self.modal and self.modal.parent:
            if self.modal is getattr(self, "tuning_modal", None):
                if codepoint in "0123456789." and codepoint:
                    self.edit_frequency(self.frequency_entry, codepoint)
                    return True
                if key in (8, 127):
                    self.edit_frequency(self.frequency_entry, "DEL")
                    return True
                if key in (13, 271):
                    self.frequency_submit()
                    return True
            return False
        if key == 276:
            self.step(-1)
        elif key == 275:
            self.step(1)
        elif codepoint == " ":
            self.toggle_rx()
        elif codepoint.lower() == "m":
            self.update_config(muted=not self.config_state.muted)
        else:
            return False
        return True

    def on_stop(self):
        if self.stopped:
            return
        self.stopped = True
        self.scanner.stop()
        self.stop_player()
        if self.client:
            self.client.close()
        self.store.save_config(self.config_state)
        self.store.close()
