#!/usr/bin/env python3
"""Live receive endurance check; never transmits or enables antenna bias."""
import argparse
import json
import os
from pathlib import Path
import sys
import time
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from touchwave.worker import Service


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--seconds', type=float, default=1800)
    parser.add_argument('--driver', default='rtlsdr', choices=['rtlsdr','hackrf'])
    parser.add_argument('--report', default='artifacts/hardware-report.json')
    parser.add_argument('--audio-device', help='Open this ALSA output with muted audio')
    args = parser.parse_args()
    import math
    if not math.isfinite(args.seconds) or args.seconds <= 0:
        parser.error("--seconds must be positive and finite")
    path = Path(args.report)
    path.parent.mkdir(parents=True, exist_ok=True)
    service = Service(path.parent/'hardware-recordings', no_audio=not args.audio_device)
    report = dict(driver=args.driver, requested_seconds=args.seconds, samples=[], result='running',
                  note='Hardware overruns may not be exposed by the driver. Inspect stderr log too.')
    try:
        devices = service.command(dict(version=1,op='discover'))
        device = next(d for d in devices if d['driver']==args.driver)
        service.command(dict(version=1,op='select',args={'device':device}))
        if args.audio_device:
            service.config=service.config.changed(audio_device=args.audio_device, muted=True)
        report['device'], report['config'] = device, service.config.to_dict()
        service.start()
        started, cpu_started = time.monotonic(), time.process_time()
        last_sample, frames = started-10, 0
        while time.monotonic()-started < args.seconds:
            if service.frame():
                frames+=1
            if not service.graph:
                raise RuntimeError(service.error or 'Stream ended unexpectedly')
            now=time.monotonic()
            if now-last_sample>=10:
                status=service.status()
                elapsed=now-started
                stat=Path('/proc/self/status').read_text()
                rss=next(line for line in stat.splitlines() if line.startswith('VmRSS:'))
                sample=dict(seconds=round(elapsed,1), frames=frames, level=status['level'],
                            telemetry=status['telemetry'], rss=rss,
                            cpu_core_percent=round(100*(time.process_time()-cpu_started)/max(elapsed,.01),1))
                report['samples'].append(sample)
                report['elapsed_seconds']=round(elapsed,1)
                path.write_text(json.dumps(report,indent=2))
                print(json.dumps(sample),flush=True)
                last_sample=now
            time.sleep(.025)
        if frames == 0:
            raise RuntimeError('No spectrum frames received')
        report.update(result='passed', elapsed_seconds=round(time.monotonic()-started,1), frames=frames)
    except Exception as exc:
        report.update(result='failed',error=str(exc))
        raise
    finally:
        service.stop()
        path.write_text(json.dumps(report,indent=2))


if __name__=='__main__':
    main()
