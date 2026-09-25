# Glossary

The integration uses the panel manufacturer's own terms, so that users recognise them from the keypad, the web interface and the official app. English terms are used in code and documentation; the German terms are used in the German translation.

The terms come from the panel's language texts and user manuals (firmware v3.01.31).

| English | German | Meaning |
|---|---|---|
| Partition | Teilbereich | An independently armed part of the installation |
| Zone | Zone | One monitored input: a wireless detector, a wired input or an IP camera |
| Detector | Melder | The device behind a zone (contact, motion detector, …) |
| Set / full set | Aktivieren / aktiv | Arm the partition completely |
| Part set | Intern aktivieren / intern aktiv | Arm the partition internally (at home) |
| Unset | Deaktivieren / deaktiv | Disarm |
| Omit zones | Zonen ausblenden | Exclude a zone from monitoring for one arming cycle |
| Fault | Störung | A condition the panel reports, e.g. an open zone or a low battery |
| Log | Logbuch | The panel's event log |
| Output | Ausgang | A switchable output of the panel |
| Acknowledge | Quittieren | Confirm an alarm |
| Reset | Rücksetzen | Return the panel to normal operation after an alarm |
| Tamper | Sabotage | Opening or manipulating a detector or the panel housing; also wrong codes ("code tamper") |
| Installer | Errichter | The installer account; while logged in at the panel, the API is locked |
| Entry time / exit time | Eingangszeit / Ausgangszeit | Delays for entering and leaving through an entry/exit zone |

States of the alarm control panel entity (e.g. "armed away") are translated by Home Assistant itself and are not part of this glossary.
