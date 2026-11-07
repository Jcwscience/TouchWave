#!/usr/bin/env python3
"""Validate real SDR mode switching/recording in Kivy, with muted USB audio."""
import os
os.environ['KIVY_NO_ARGS']='1'
from pathlib import Path
import sys
import time
import json
import tempfile
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from kivy.config import Config
Config.set('graphics','width','1080')
Config.set('graphics','height','1240')
from kivy.clock import Clock
from kivy.core.window import Window
from touchwave.ui.app import TouchWaveApp

out=Path('artifacts');out.mkdir(exist_ok=True)
app=TouchWaveApp(data_path=tempfile.mkdtemp(prefix='live-ui-',dir=out))
app.config_state=app.config_state.changed(muted=True)
report={'result':'running','modes':[]}
stage='ready'
started=time.monotonic()
changed=started
mode_index=0
modes=['NFM','AM','USB','LSB','WFM']
failed=False


def tick(dt):
    global stage,changed,mode_index,failed
    now=time.monotonic()
    try:
        if now-started>90:
            raise AssertionError('Live GUI check timed out')
        if app.state.get('error'):
            raise AssertionError(app.state['error'])
        if stage=='ready' and not app.initializing and app.state and not app.pending_config:
            assert app.state['device']['driver']=='rtlsdr',app.state['device']
            app.toggle_rx()
            stage='initial'
        elif stage=='initial' and app.phase=='receiving' and app.plot.spectrum is not None:
            app.set_mode(modes[mode_index])
            stage='modes'
            changed=now
        elif stage=='modes' and not app.pending_config and app.phase=='receiving' and now-changed>2:
            assert app.config_state.mode==modes[mode_index]
            assert app.plot.spectrum is not None
            report['modes'].append(dict(mode=app.config_state.mode,level=app.state['level'],result='passed'))
            mode_index+=1
            if mode_index<len(modes):
                app.set_mode(modes[mode_index])
                changed=now
            else:
                app.client.send('record',kind='audio',duration=1,max_bytes=1024**2)
                stage='audio'
        elif stage=='audio' and any(r['kind']=='audio' for r in app.store.list_recordings()):
            app.client.send('record',kind='iq',duration=1,max_bytes=8*1024**2)
            stage='iq'
        elif stage=='iq' and any(r['kind']=='iq' for r in app.store.list_recordings()):
            stage='history'
            changed=now
        elif stage=='history' and now-changed>14:
            actual=Window.screenshot(name=str(out/'receiver-rtl.png'))
            Path(actual).replace(out/'receiver-rtl.png')
            report.update(result='passed',config=app.config_state.to_dict(),device=app.state['device'],
                          recordings=app.store.list_recordings(),telemetry=app.state['telemetry'],
                          elapsed_seconds=round(now-started,1))
            finish()
            return False
    except Exception as exc:
        import traceback
        traceback.print_exc()
        failed=True
        report.update(result='failed',error=str(exc),stage=stage)
        finish()
        return False


def finish():
    (out/'live-ui-report.json').write_text(json.dumps(report,indent=2))
    app.stop()

Clock.schedule_interval(tick,.1)
app.run()
print(json.dumps(report,indent=2))
raise SystemExit(1 if failed else 0)
