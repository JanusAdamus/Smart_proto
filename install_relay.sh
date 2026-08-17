#!/bin/bash
set -e
sudo apt-get update
sudo apt-get install -y python3-zeroconf python3-serial python3-ifaddr
id -u relay >/dev/null 2>&1 || sudo useradd -r -s /usr/sbin/nologin relay
# Group membership only applies to new logins/process starts; if this script
# is re-run on an already-running Pi, `sudo systemctl restart relay` after.
sudo usermod -aG dialout relay
sudo mkdir -p /opt/smartmeter
sudo cp relay.py discovery.py serial_link.py /opt/smartmeter/
sudo cp relay.service /etc/systemd/system/relay.service
sudo systemctl daemon-reload
sudo systemctl enable --now relay
echo "Relay instalado y corriendo. Ver logs: journalctl -u relay -f"
