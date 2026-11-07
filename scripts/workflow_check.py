#!/usr/bin/env python3
"""Exercise complete GUI workflows using demo RF and an isolated data profile."""
import os
os.environ['KIVY_NO_ARGS']='1'
from pathlib import Path
import sys
import tempfile
import time
import json
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from kivy.config import Config
Config.set('graphics','width','1080')
Config.set('graphics','height','1240')
Config.set('kivy','keyboard_mode','systemanddock')
from kivy.clock import Clock
from kivy.uix.textinput import TextInput
from touchwave.ui.app import TouchWaveApp

app=TouchWaveApp(demo=True,no_audio=True,data_path=tempfile.mkdtemp(prefix='touchwave-workflow-'))
report={'result':'running','steps':[]}
steps=[]

def step(name,action,predicate=lambda:True,timeout=8):
    steps.append((name,action,predicate,timeout))

def button(text):
    return next(w for w in app.modal.walk() if getattr(w,'text',None)==text)

def inputs():
    # Body insertion order is the reverse of Kivy's children list.
    return sorted((w for w in app.modal.walk() if isinstance(w,TextInput)),key=lambda w:-w.y)

def dismiss():
    if app.modal:
        app.modal.dismiss()

def type_frequency():
    assert app.frequency_entry.selection_text
    for char in '103.5':
        app.key_down(None,ord(char),0,char,[])
    assert app.frequency_entry.text=='103.5',app.frequency_entry.text
    app.key_down(None,13,0,'',[])

def save_bookmark():
    fields=inputs()
    fields[0].text='Test local'
    fields[1].text='local, test'
    button('Save current frequency').dispatch('on_release')

def record(kind):
    app.record_dialog()
    Clock.schedule_once(lambda dt:record_fields(kind),.15)

def record_fields(kind):
    fields=inputs()
    fields[0].text='1'
    fields[1].text='1'
    name='Record audio · stereo WAV' if kind=='audio' else 'Record IQ · SigMF'
    button(name).dispatch('on_release')

def replay():
    row=next(r for r in app.store.list_recordings() if r['kind']=='iq')
    app.library_dialog()
    app.play_recording(row)

def start_scan():
    app.store.add_bookmark('Test other','test',app.config_state.changed(frequency=104100000))
    app.scan_dialog()
    Clock.schedule_once(lambda dt:button('Start scanning').dispatch('on_release'),.2)

def gestures():
    from types import SimpleNamespace
    def touch(ident,x):
        t=SimpleNamespace(uid=ident,x=x,y=app.plot.center_y,pos=(x,app.plot.center_y),is_mouse_scrolling=False,is_double_tap=False,grab_current=None)
        t.grab=lambda widget:setattr(t,'grab_current',widget)
        t.ungrab=lambda widget:setattr(t,'grab_current',None)
        return t
    p=app.plot
    a,b=touch('a',p.x+p.width*.3),touch('b',p.x+p.width*.7)
    p.on_touch_down(a);p.on_touch_down(b)
    b.x=p.x+p.width*.9;b.pos=(b.x,b.y)
    p.on_touch_move(b)
    assert p.zoom>1
    p.on_touch_up(a);p.on_touch_up(b)
    c=touch('c',p.x+p.width*.6)
    p.on_touch_down(c);p.on_touch_up(c)

step('ready',lambda:None,lambda:not app.initializing and bool(app.state) and not app.pending_config)
step('open keypad',app.frequency_dialog,lambda:bool(app.frequency_entry.selection_text))
step('physical frequency entry replaces selection',type_frequency,lambda:not app.pending_config and app.config_state.frequency==103500000)
step('select NFM',lambda:app.set_mode('NFM'),lambda:not app.pending_config and app.config_state.mode=='NFM')
step('bookmark sheet',app.bookmark_dialog)
step('save tagged bookmark',save_bookmark,lambda:len(app.store.bookmarks())==1)
step('start receiver',app.toggle_rx,lambda:app.phase=='receiving')
step('bounded WAV through GUI',lambda:record('audio'),lambda:any(r['kind']=='audio' for r in app.store.list_recordings()))
step('bounded IQ through GUI',lambda:record('iq'),lambda:any(r['kind']=='iq' for r in app.store.list_recordings()))
step('replay IQ through library',replay,lambda:bool(app.state.get('replay')))
step('return to live',lambda:app.client.send('live'),lambda:not app.state.get('replay') and app.phase=='idle')
step('set scan threshold',lambda:app.update_config(squelch=0),lambda:not app.pending_config)
step('resume receiver',app.toggle_rx,lambda:app.phase=='receiving')
step('start bookmark scan',start_scan,lambda:app.scanner.active)
step('manual tuning stops scan',lambda:app.step(1),lambda:not app.scanner.active and not app.pending_config)
step('multitouch zoom and tap tune',gestures,lambda:not app.pending_config)
step('restart worker',app.start_worker,lambda:bool(app.state) and app.phase=='idle' and not app.pending_config)

current=None
start=0
failed=False

def tick(dt):
    global current,start,failed
    try:
        if current is None:
            if not steps:
                report['result']='passed'
                finish()
                return False
            current=steps.pop(0)
            start=time.monotonic()
            current[1]()
        if current[2]():
            report['steps'].append({'name':current[0],'seconds':round(time.monotonic()-start,2),'result':'passed'})
            current=None
        elif time.monotonic()-start>current[3]:
            raise AssertionError(f'Timed out: {current[0]} / {app.notice_label.text}')
    except Exception as exc:
        import traceback
        traceback.print_exc()
        failed=True
        report.update(result='failed',error=str(exc),step=current[0] if current else None)
        finish()
        return False

def finish():
    out=Path('artifacts');out.mkdir(exist_ok=True)
    (out/'workflow-report.json').write_text(json.dumps(report,indent=2))
    app.stop()

Clock.schedule_interval(tick,.1)
app.run()
print(json.dumps(report,indent=2))
raise SystemExit(1 if failed else 0)
