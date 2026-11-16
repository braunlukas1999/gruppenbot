# Anleitung: Dein Gruppen-Bot, nur mit dem Handy eingerichtet

Du brauchst keinen PC. Alles geht im Handy-Browser und in Telegram.

---

## Was der Bot kann

**1. Prüfung für neue Mitglieder**
- Wer neu beitritt, wird stummgeschaltet. In General erscheint eine Begrüßung mit Link zu den Regeln und dem Knopf „✅ Ich habe die Regeln gelesen“.
- Danach kommt eine leichte Rechenaufgabe mit 4 Antwort-Knöpfen.
- Richtig: freigeschaltet. Zweimal falsch oder 5 Minuten nichts gemacht: wird entfernt (kann neu beitreten).

**2. Regeln pro Thema, für alle deine Gruppen**
- Die Regeln hängen am **Namen des Themas**. Heißt ein Thema in irgendeiner deiner Gruppen „Fotos“, gilt dort automatisch „nur Fotos“.
- Groß/klein und Emojis sind egal: „📸 Fotos“ zählt als „Fotos“.
- Was nicht erlaubt ist, wird gelöscht, mit kurzem Hinweis (verschwindet nach 10 Sekunden).
- Admins dürfen überall alles. Themen ohne Regel: alles erlaubt.

| Thema | Erlaubt |
|---|---|
| General, Regeln | nur Admins |
| RP Anfragen, DM Anfragen, Links | nur Text |
| Chat, Vorschläge | Text + Fotos |
| Risk and Catch | Text + Fotos + Videos |
| Fotos | nur Fotos |
| Videos | nur Videos |

**3. Meldungen mit /report und /admin**
- Mitglied antwortet auf eine Nachricht mit `/report` (gern mit Grund: `/report Fake-Link`).
- Jeder Admin bekommt privat die Meldung mit Link und Knöpfen: **🗑 Löschen**, **🔇 24h stumm**, **🚫 Bannen**, **✅ Erledigt**.
- `/admin` ohne Antwort = allgemeiner Hilferuf.
- **Jeder Admin muss den Bot einmal privat öffnen und auf „Starten“ tippen**, sonst darf der Bot ihm nichts schicken.

---

## Einrichtung (machen wir zusammen Schritt für Schritt im Chat)

**A. Bot bei Telegram anlegen**
1. **@BotFather** öffnen (blauer Haken), `/newbot` schreiben, Namen und Benutzernamen (endet auf `bot`) eingeben.
2. Den **Token** kopieren (`7234567890:AAH...`). Niemandem zeigen!
3. Bot zur Gruppe hinzufügen, zum **Admin** machen: ✅ Nachrichten löschen ✅ Mitglieder sperren ✅ Themen verwalten.
4. Bei Rose Captcha und Begrüßung ausschalten: `/captcha off` und `/welcome off`.

**B. Gratis-Server bei Oracle** (im Handy-Browser „Desktop-Version“ anhaken)
1. **oracle.com/de/cloud/free** → Kostenlos starten. Home-Region: **Germany Central (Frankfurt)**. Kreditkarte nur zur Prüfung.
2. Menü (☰) → **Compute** → **Instances** → **Create instance**, Name `gruppenbot`.
3. Image: **Canonical Ubuntu**. Shape: **VM.Standard.E2.1.Micro** („Always Free-eligible“).
4. SSH keys: **No SSH keys**.
5. **Show advanced options** → **Management** → **Paste cloud-init script**, diesen Text einfügen und **DEIN_TOKEN** ersetzen:
   ```
   #!/bin/bash
   apt-get update -y && apt-get install -y git
   git clone https://github.com/braunlukas1999/gruppenbot.git /opt/gruppenbot
   echo "DEIN_TOKEN" > /opt/gruppenbot/token.txt
   bash /opt/gruppenbot/installieren_server.sh
   ```
6. **Create**, dann 5 Minuten warten. Test: Dem Bot privat `/start` schreiben.

**C. In der Gruppe**
1. Der Bot lernt die Themen-Namen von selbst, sobald in einem Thema geschrieben wird. Am schnellsten: als Admin in jedes Thema kurz „.“ schreiben und wieder löschen.
2. `/einstellungen` in der Gruppe zeigt, was wo gilt.

---

## Befehle (nur für Admins, in der Gruppe)

| Befehl | Was er macht |
|---|---|
| `/einstellungen` | Zeigt alle Themen dieser Gruppe und ihre Regeln |
| `/regeln` | Zeigt die Regeln für alle Gruppen |
| `/regeln` + Liste | Regeln für **alle Gruppen** festlegen (siehe unten) |
| `/erlaubt Text und Fotos` | Ausnahme im aktuellen Thema, **nur in dieser Gruppe** |
| `/erlaubt alles` | Im aktuellen Thema alles erlauben (nur diese Gruppe) |
| `/nuradmins` | Im aktuellen Thema schreiben nur Admins (nur diese Gruppe) |
| `/standard` | Ausnahme im aktuellen Thema wieder löschen |
| `/thema Name` | Dem Bot sagen, wie das Thema heißt (nur falls er es nicht selbst erkennt) |
| `/update` | Bot neu starten und neueste Version holen |

**Regeln per Nachricht festlegen**
```
/regeln
Memes: Fotos und GIFs
Chat: Text, Fotos und Sticker
Ankündigungen: nur Admins
Spielwiese: alles
```
Der Bot antwortet, was er verstanden hat. Die Regeln gelten sofort in **allen** Gruppen mit einem Thema dieses Namens, auch in neuen. Du kannst auch die Themen-Liste aus deinem Regeltext einfügen (z. B. „🖼 Fotos – nur Fotos“).

Bekannte Wörter: Text, Fotos/Bilder, Videos, GIFs, Sticker, Sprachnachrichten, Audio, Dateien, Videonachrichten, Umfragen, Standort, Kontakt, alles, nur Admins.

**Neue Gruppe:** Bot hinzufügen und zum Admin machen. Fertig.

---

## Gut zu wissen

- **General und Regeln schließen:** Thema lange antippen → **Thema schließen**. Der Bot kann trotzdem dort posten.
- **Oracle schaltet den Server ab?** Kann bei Gratis-Servern passieren, du bekommst eine E-Mail. Oracle → Compute → Instances → gruppenbot → **Start**.
- **Neue Funktionen:** Nach einer Verbesserung einfach `/update` in die Gruppe schreiben.
- **Jemand hängt stummgeschaltet fest:** Person in der Mitgliederliste antippen und Rechte von Hand zurückgeben.
