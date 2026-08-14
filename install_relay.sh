#!/bin/bash
set -e
sudo apt-get update
sudo apt-get install -y python3-zeroconf python3-serial
sudo mkdir -p /opt/smartmeter
sudo cp relay.py discovery.py serial_link.py /opt/smartmeter/
sudo cp relay.service /etc/systemd/system/relay.service
sudo systemctl daemon-reload
sudo systemctl enable --now relay
echo "Relay instalado y corriendo. Ver logs: journalctl -u relay -f"
