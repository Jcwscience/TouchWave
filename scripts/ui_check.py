#!/usr/bin/env python3
"""Render native-size receiver and sheets against synthetic RF on the local GPU."""
import os
os.environ['KIVY_NO_ARGS']='1'
from pathlib import Path
import sys
import tempfile
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from kivy.config import Config
Config.set('graphics','width','1080')
Config.set('graphics','height','1240')
Config.set('graphics','maxfps','30')
Config.set('kivy','keyboard_mode','systemanddock')
from kivy.clock import Clock
from kivy.core.window import Window
from touchwave.ui.app import TouchWaveApp

out=Path('artifacts');out.mkdir(exist_ok=True)
app=TouchWaveApp(demo=True,no_audio=True,data_path=tempfile.mkdtemp(prefix='touchwave-ui-'))

def snapshot(name):
    actual=Window.screenshot(name=str(out/(name+'.png')))
    Path(actual).replace(out/(name+'.png'))

def sheet(fn, name):
    if app.modal:
        app.modal.dismiss()
    fn()
    Clock.schedule_once(lambda dt:snapshot(name), .6)

Clock.schedule_once(lambda dt:app.toggle_rx(),2)
Clock.schedule_once(lambda dt:snapshot('receiver-live'),15)
Clock.schedule_once(lambda dt:sheet(app.frequency_dialog,'frequency-entry'),16)
Clock.schedule_once(lambda dt:sheet(app.settings_dialog,'controls'),18)
Clock.schedule_once(lambda dt:sheet(app.view_dialog,'plot-controls'),20)
Clock.schedule_once(lambda dt:sheet(app.bookmark_dialog,'bookmark'),22)
Clock.schedule_once(lambda dt:sheet(app.library_dialog,'library'),24)
def finish(dt):
    import json
    report=dict(phase=app.phase, worker_alive=app.client.process.poll() is None,
                fps=Clock.get_fps(), errors=app.state.get("error"),
                screenshots=[p.name for p in out.glob("*.png") if "000" not in p.name])
    (out/'ui-report.json').write_text(json.dumps(report,indent=2))
    assert app.phase=='receiving' and not app.state.get('error'), report
    app.stop()
Clock.schedule_once(finish,26)
app.run()
