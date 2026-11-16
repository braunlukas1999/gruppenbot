#!/bin/bash
# Richtet den Bot auf einem Ubuntu-Server ein, sodass er 24/7 laeuft,
# nach einem Neustart automatisch wieder startet und sich bei jedem
# Start (z. B. mit /update in Telegram) die neueste Version von GitHub holt.
set -e
cd "$(dirname "$0")"
DIR="$(pwd)"
export DEBIAN_FRONTEND=noninteractive
apt-get update -y
apt-get install -y python3 python3-venv git
python3 -m venv venv
venv/bin/pip install -r requirements.txt
cat > /etc/systemd/system/gruppenbot.service <<UNIT
[Unit]
Description=Telegram Gruppen-Bot
After=network-online.target
Wants=network-online.target

[Service]
WorkingDirectory=$DIR
ExecStartPre=-/usr/bin/git -C $DIR pull --ff-only
ExecStartPre=-$DIR/venv/bin/pip install -q -r $DIR/requirements.txt
ExecStart=$DIR/venv/bin/python $DIR/bot.py
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
UNIT
systemctl daemon-reload
systemctl enable gruppenbot
systemctl restart gruppenbot
sleep 3
systemctl status gruppenbot --no-pager | head -15
echo
echo "Fertig! Der Bot läuft jetzt dauerhaft."
