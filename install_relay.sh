#!/bin/bash
set -e
sudo apt-get update
sudo apt-get install -y python3-pip
sudo pip3 install zeroconf
sudo mkdir -p /opt/smartmeter
sudo cp relay.py discovery.py /opt/smartmeter/
sudo cp relay.service /etc/systemd/system/relay.service
sudo systemctl daemon-reload
sudo systemctl enable --now relay
echo "Relay instalado y corriendo. Ver logs: journalctl -u relay -f"
