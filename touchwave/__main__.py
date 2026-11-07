"""CLI entry point. Diagnostic commands do not import the GUI."""
import argparse
import json


def main():
    parser = argparse.ArgumentParser(description="TouchWave handheld SDR")
    parser.add_argument("--demo", action="store_true", help="Select synthetic RF source (reception starts manually)")
    parser.add_argument("--windowed", action="store_true", help="540 × 620 desktop preview")
    parser.add_argument("--no-audio", action="store_true", help="Run DSP with silent output for diagnostics")
    parser.add_argument("--devices", action="store_true", help="List connected SDRs and capabilities")
    parser.add_argument("--data-dir", help="Override the XDG data directory")
    parser.add_argument("--smoke-seconds", type=float, help="Automatically close UI after a diagnostic interval")
    parser.add_argument("--screenshot", help="Save UI screenshot before diagnostic shutdown")
    args = parser.parse_args()
    if args.devices:
        from .devices import SoapyProvider
        provider = SoapyProvider()
        print(json.dumps([dict(device=d, capabilities=provider.capabilities(d)) for d in provider.discover()], indent=2))
        return
    # Keep application arguments away from Kivy's own CLI parser.
    import os
    os.environ["KIVY_NO_ARGS"] = "1"
    from kivy.config import Config
    Config.set("graphics", "width", "540" if args.windowed else "1080")
    Config.set("graphics", "height", "620" if args.windowed else "1240")
    Config.set("graphics", "maxfps", "30")
    Config.set("kivy", "keyboard_mode", "systemanddock")
    from .ui.app import TouchWaveApp
    app = TouchWaveApp(demo=args.demo, no_audio=args.no_audio, fullscreen=not args.windowed, data_path=args.data_dir)
    if args.smoke_seconds:
        from kivy.clock import Clock
        def finish(dt):
            if args.screenshot:
                app.root.export_to_png(args.screenshot)
            app.stop()
        Clock.schedule_once(finish, args.smoke_seconds)
    app.run()


if __name__ == "__main__":
    main()
