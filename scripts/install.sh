#!/usr/bin/env bash
set -euo pipefail
cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.."
sudo apt-get update
sudo apt-get install -y python3-venv python3-setuptools python3-kivy python3-numpy python3-zmq python3-soapysdr python3-pytest gnuradio soapysdr-module-rtlsdr soapysdr-module-hackrf alsa-utils
/usr/bin/python3 -m venv --system-site-packages .venv
.venv/bin/python -m pip install --no-deps --no-build-isolation -e .
.venv/bin/python scripts/install_launcher.py
