#!/bin/bash
# Richtet den Bot auf einem Ubuntu-Server ein, sodass er 24/7 laeuft,
# nach einem Neustart automatisch wieder startet und sich bei jedem
# Start (z. B. mit /update in Telegram) die neueste Version von GitHub holt.
#
# Fortschritt und Fehler werden in die Profil-Beschreibung des Bots geschrieben
# (in Telegram: Bot-Profil oeffnen), damit man ohne PC sieht, was passiert.
set -E
cd "$(dirname "$0")"
DIR="$(pwd)"
exec > >(tee -a /var/log/gruppenbot-setup.log) 2>&1
TOKEN="$(tr -d ' \r\n' < "$DIR/token.txt" 2>/dev/null)"

status() {
  echo "STATUS: $1"
  [ -n "$TOKEN" ] && curl -s -m 20 "https://api.telegram.org/bot$TOKEN/setMyShortDescription" \
    --data-urlencode "short_description=$1" > /dev/null
}
fail() { status "❌ Einrichtung-Fehler bei: $1 ($(date +%H:%M))"; exit 1; }

status "⏳ Einrichtung 1/4: Server-Pakete ($(date +%H:%M))"
export DEBIAN_FRONTEND=noninteractive
# Beim ersten Start installiert Ubuntu oft selbst Updates. Dann warten wir, statt abzubrechen.
APT="apt-get -o DPkg::Lock::Timeout=900"
ok=; for i in 1 2 3; do $APT update -y && ok=1 && break; sleep 20; done; [ -n "$ok" ] || fail "apt update"
ok=; for i in 1 2 3; do $APT install -y python3 python3-venv python3-pip git && ok=1 && break; sleep 20; done; [ -n "$ok" ] || fail "apt install"

status "⏳ Einrichtung 2/4: Python ($(date +%H:%M))"
python3 -m venv venv || fail "venv"
ok=; for i in 1 2 3; do venv/bin/pip install -r requirements.txt && ok=1 && break; sleep 20; done; [ -n "$ok" ] || fail "pip install"

status "⏳ Einrichtung 3/4: Dienst ($(date +%H:%M))"
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
systemctl enable gruppenbot || fail "systemctl enable"
status "⏳ Einrichtung 4/4: Bot startet ($(date +%H:%M))"
systemctl restart gruppenbot || fail "Bot-Start"
sleep 15
systemctl is-active --quiet gruppenbot || fail "Bot läuft nicht: $(journalctl -u gruppenbot -n 3 --no-pager | tail -c 80)"
echo "Fertig! Der Bot läuft jetzt dauerhaft."
