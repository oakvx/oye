# Oye 37 — Web Studio

## Interfaccia

La finestra predefinita è una pagina **HTML/CSS/JavaScript locale** servita da `oye_web_app.py` solo su `127.0.0.1`, con un token casuale per sessione. Non viene caricato codice JavaScript da CDN.

Pagine:

1. **Panoramica**: modalità Ignorante, Ciclo continuo, Una partita, Osserva, Registra e Esplora; anteprima, telemetria, WASD/mouse, eventi, Stop e Pausa.
2. **Insegna a Oye**: acquisizione guidata, OCR parziale, insegnamento del testo e riferimenti visivi, selezione manuale opzionale.
3. **Le mie partite**: galleria degli screenshot reali di `learning/frames/`, filtri per training e sessione, esportazione JSONL.
4. **Timeline editor**: fotogrammi ordinati per data, scrubbing, riproduzione dei frame registrati (non un video ricostruito), tracce eventi/input, revisione di un singolo evento o azione, annotazione di intervalli, conferma delle dimostrazioni e note di sessione.
5. **Impostazioni**: controllo di movimento, OCR, uso dei training, recupero ostacoli, Ollama locale, mappatura tasti, diagnostica manuale W e telecamera.

Animazioni: ambienti luminosi, navigazione con transizioni, pulsazione degli stati attivi, movimento delle linee di scansione, riflessi sui pulsanti, feedback di hover, highlight dinamico dei comandi. Con `prefers-reduced-motion: reduce` le animazioni sono disattivate.

Il frontend non gestisce il driver: le chiamate passano alle stesse classi `Runner`, `Dashboard` e `LearningStore` della v36. Gli input LIVE richiedono conferma e focus della finestra autorizzata. L'OCR e la modalità Ignorante mantengono le regole della v36.

## Archivio esistente

Questo aggiornamento **non contiene** `learning/`, `templates/`, `config.json`, `state/`, `logs/`, screenshot né database. Estraendolo sopra la cartella esistente si conservano gli apprendimenti. Il Web Studio legge e scrive **lo stesso** `learning/observer.sqlite3`; non crea un database parallelo. Prima di aggiornare, chiudere Oye ed effettuare una copia di sicurezza della cartella.

Le immagini possono mancare per fotogrammi molto vecchi quando il limite di archiviazione le ha eliminate. In questo caso le etichette SQLite restano visibili.

## Avvio

- `AVVIA_OYE.bat`: preflight del runtime, apertura WebView2 o browser. Al primo avvio può richiedere Internet.
- `apri_studio.bat`: alias dello stesso avvio.
- `apri_studio_browser.bat`: usa il browser standard.
- `apri_editor_avanzato.bat`: apre direttamente la **Timeline web**, senza Tkinter.

L'eseguibile installer non è contenuto nello ZIP sorgente. `build_exe_windows.bat` su Windows genera un installer tramite PyInstaller e Inno Setup, se installati. La compilazione Windows **non è stata provata** su questa macchina.

## API locali (token obbligatorio)

- `GET /api/status`, `/api/frame`, `/api/calibration/frame` — telemetria e immagini.
- `GET /api/learning?session=...` — frame e dati di training, senza feature binarie SQLite.
- `GET /api/learning/image?id=...` — solo immagini JPEG già referenziate nel database.
- `GET /api/learning/export?session=...` — metadati JSONL.
- `POST /api/learning/edit` — operazioni consentite `event`, `action`, `interval`, `delete_interval`, `confirm_demo`, `bulk` e `note`.
- `POST /api/input-test` — diagnostica LIVE esplicita, mai eseguita automaticamente.

Nessuna API può leggere path arbitrari dal browser, e tutte le scritture dell'editor sono validate dal `LearningStore`.

## Verifiche e limiti

Esegui `python -m unittest discover -s tests -q` e `python studio.py --smoke-test`.

Le verifiche disponibili coprono HTML/CSS/JS, API, archivio SQLite, scritture, autenticazione e preservazione dati. Sono ancora da verificare l'installer Windows, la resa WebView2 sul PC dell'utente e la ricezione degli input da parte di Fortnite. Non sono inserite tecniche per aggirare anti-cheat.

Per compatibilità, i vecchi sorgenti `studio_legacy.py` e `studio_qt.py` restano nell'archivio, ma **i pulsanti principali e gli script di avvio non li utilizzano**. Alcune utilità storiche minori (per esempio importazione manuale da una seconda cartella arbitraria e gestione audio) non hanno ancora un equivalente nella dashboard. Non eliminare i vecchi dati.
