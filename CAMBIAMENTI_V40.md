# Oye v40 — Registro modifiche e limiti

## P0 risolti
- La lettura del pulsante Gioca distingue `PRESENTE`, `ASSENTE` verificato e `SCONOSCIUTO`; assenza di OCR e frame vecchi non avviano il timer.
- Il controllo iniziale della dashboard considera il profilo 1920×1080 e i riferimenti OCR del profilo, non soltanto i vecchi file `templates/`. L'errore su un vecchio PNG corrotto non invalida un nuovo riferimento valido.

## Componenti nuovi o modificati
- **Nuovi:** `observer/visual_scanners.py`, `observer/scanner_store.py`, `observer/state_recognition.py`, `web/scanners.html`, `web/scanners.css`, `web/scanners.js`, `replay_scanners.py`, `tests/test_v40_scanners.py`, `tests/test_v40_http.py`, `docs/OYE_V40_VISUAL_SCANNERS.md`.
- **Integrati:** `observer/region_ocr.py` (scanner su frame condiviso), `observer/runner.py` (fusion nel controller esistente), `observer/single_match.py` (Gioca a tre stati), `observer/vision.py` (segnali visivi verificati), `observer/screen_profiles.py` (rimozione coerente e connessioni SQLite), `oye_web_app.py` (servizio e API), `web/index.html`, `web/mini.html`, script/build/docs/versione.

## Migrazione e conservazione dati
Le tabelle `oye_v40_scanners` e `oye_v40_scanner_examples` si aggiungono al database SQLite precedente; gli esempi immagine sono sotto `learning/scanner_examples/`. Non è prevista la cancellazione o conversione distruttiva degli apprendimenti. Lo ZIP non contiene `learning/`, `templates/` né `config.json`: l'estrazione sopra un'installazione preesistente non li sovrascrive. Sono preservati anche i vecchi script e gli editor legacy per compatibilità.

## Test e limiti
Esegui `python -m unittest discover -s tests -q`, `python studio.py --smoke-test`, `node --check web/scanners.js`, e `python replay_scanners.py --folder <dir> --root . --output replay.jsonl`. I test degli elementi HUD sono sintetici e con emulatori del risultato OCR; l'accuratezza su Fortnite **non è stata validata**. L'interfaccia web è stata verificata tramite HTTP e sorgenti statici, non tramite un browser Windows collegato a Fortnite. Il metodo `model` resta non operativo finché non è installato un backend specifico; il punteggio di confidenza è euristico, non una probabilità calibrata. L'EXE Windows e l'input effettivamente ricevuto dal gioco non sono stati collaudati.
