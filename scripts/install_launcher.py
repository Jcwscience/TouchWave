#!/usr/bin/env python3
"""Install a per-user desktop launcher; autostart is opt-in via --autostart."""
import argparse
import os
from pathlib import Path
import sys
parser=argparse.ArgumentParser()
parser.add_argument('--autostart',action='store_true')
args=parser.parse_args()
root=Path(__file__).resolve().parents[1]
# Desktop Entry Exec quoting differs from shell quoting. Escape reserved characters.
def quote(value):
    return '"'+str(value).replace('\\','\\\\').replace('"','\\"').replace('`','\\`').replace('$','\\$').replace('%','%%')+'"'
text='\n'.join(['[Desktop Entry]','Type=Application','Name=TouchWave',
                'Comment=Handheld software-defined radio',
                f'Exec={quote(sys.executable)} -m touchwave',
                f'Path={root}',f'Icon={root / "touchwave/ui/assets/touchwave.svg"}','Terminal=false',
                'Categories=AudioVideo;Audio;HamRadio;','StartupNotify=true',''])
applications=Path(os.environ.get('XDG_DATA_HOME',Path.home()/'.local/share'))/'applications'
applications.mkdir(parents=True,exist_ok=True)
(applications/'touchwave.desktop').write_text(text)
print(applications/'touchwave.desktop')
if args.autostart:
    autostart=Path(os.environ.get('XDG_CONFIG_HOME',Path.home()/'.config'))/'autostart'
    autostart.mkdir(parents=True,exist_ok=True)
    (autostart/'touchwave.desktop').write_text(text)
    print(autostart/'touchwave.desktop')
