# Oye v41 — Correzioni degli scanner e stabilità degli stati

**Base:** v40. Aggiornamento incrementale: nessuna migrazione distruttiva e nessun secondo motore di gioco. Il profilo dell'utente resta associato alla risoluzione selezionata (es. 1920×1080, senza bordi).

## Bug corretti

| Priorità | Difetto nella v40 | Correzione v41 |
| --- | --- | --- |
| P0 | Rumore visivo classificato come bussola, minimappa, vita o inventario | Rilevamento basato su geometria coerente (tacche verticali, cornici, bande orizzontali, slot); contenuti casuali restituiscono `unknown`. Gli scanner strutturali non forniscono evidenze per il controller senza almeno un esempio positivo e uno negativo. |
| P0 | `IN_MATCH` poteva restare confermato all'infinito senza nuovi frame | `StateRecognitionEngine` applica il timeout anche senza nuove osservazioni; dopo la scadenza torna a `UNKNOWN`. |
| P1 | Test ROI prima di salvare usava la vecchia configurazione | `/api/scanners/test` accetta una bozza completa, la valida senza scrivere nel database e restituisce l'anteprima della stessa ROI. |
| P1 | Importazione parziale dei preset | Validazione preventiva di tutte le voci, rifiuto dei duplicati, transazione SQLite unica. |
| P1 | Gli stati modulari venivano usati prima di verificarne l'età | Il Runner scarta risposte asincrone scadute e usa per pulsanti/clic solo scanner eseguiti sul frame effettivamente restituito. |
| P1 | OCR prioritari ritardati/accumulo di timeout | Scheduler condiviso per frame con priorità per fase, avanzamento per scadenze e al massimo **un OCR costoso per frame** fra vecchio e nuovo sistema. |
| P1 | Replay diverso dall'esecuzione reale | Stesso scheduler modulare del worker; il replay considera anche le regole OCR legacy e lo `stage` opzionale nel manifest. |
| P2 | Connessioni SQLite non chiuse | Chiusura esplicita di connessioni del ciclo, backup, riepiloghi e migrazione; test aggiornati per chiudere le connessioni temporanee. |
| Integrazione | Le correzioni agli esempi non aggiornavano lo stato di calibrazione | Ricaricamento del worker dopo aggiunta/rimozione degli esempi; la ricarica avviene sul thread del worker, non mentre esegue uno scanner. |

## Importante: due comportamenti distinti

**Prova scanner:** il risultato grezzo viene mostrato nell'editor per calibrare la ROI e le soglie. Anche uno scanner senza esempi può essere provato.

**Riconoscimento operativo:** i risultati degli scanner `structure` senza esempi positivi e negativi sono degradati a `unknown`, non autorizzano a classificare `IN_MATCH` né a inviare input. Questo vale anche per il replay offline. I preset nuovi restano disabilitati finché non configurati.

L'editor mostra `DA CALIBRARE` finché non sono presenti entrambe le categorie di esempi. La presenza di due etichette non è una misura di accuratezza: servono esempi veri e diversi, una verifica su più partite e un dataset rappresentativo.

## Replay offline

```bash
python replay_scanners.py --folder ./miei_screenshot --root . --interval 0.5 --output replay-v41.jsonl
```

Un manifest può specificare la fase per ciascuna immagine: 

```json
[
  {"file":"001.png","timestamp":0.0,"stage":"await_play"},
  {"file":"002.png","timestamp":0.5,"stage":"joining"}
]
```

I risultati contengono `state`, `scanners`, `legacy_readings`, `scheduler.executed`, `scheduler.legacy_ocr_count`, `scheduler.stage`. Il replay **non** riproduce il funzionamento di Windows, degli input o delle latenze reali del PC: verifica soltanto le decisioni offline usando i frame e le tempistiche fornite.

## File principali modificati

- `observer/visual_scanners.py`: pattern strutturali selettivi.
- `observer/state_recognition.py`: scadenza anche senza nuovi frame.
- `observer/scanner_scheduler.py`: scheduler comune per live e replay (nuovo).
- `observer/region_ocr.py`: pianificazione OCR, calibrazione e ricarica sicura.
- `observer/runner.py`: controlli di freschezza prima di promuovere stati o coordinate.
- `observer/scanner_store.py`: importazione atomica, validazione delle bozze, dati di calibrazione.
- `oye_web_app.py`, `web/scanners.js`: prova ROI non salvata e aggiornamento degli esempi.
- `replay_scanners.py`: riproduzione con scheduling, memoria e legacy OCR.
- `observer/cycle_db.py`, `observer/upgrade.py`, `observer/migration.py`: gestione risorse SQLite.
- `tests/test_v41_regressions.py`: nuovi test di regressione.
- Script Windows e workflow: numero di versione aggiornato, stesso percorso dati e stesso AppId dell'installer per gli aggiornamenti.

## Verifiche e limiti

Verificati in questo ambiente: **228 test eseguiti, 227 superati, uno saltato**, verifica JavaScript con Node e test HTTP locale con `studio.py --smoke-test`. Test negativi su immagini casuali e fixture sintetiche. Queste prove **non equivalgono** a una misura dell'accuratezza su screenshot reali di Fortnite.

Non verificati qui: input sul gioco, cattura della finestra Windows, riconoscimento su partite reali, compilation dell'installer `.exe`. La precisione resta condizionata alla calibrazione delle ROI, alla scala HUD e agli esempi reali positivi/negativi.

## Aggiornamento senza perdita dati

Arresta Oye, crea una copia di sicurezza della cartella e **estrai lo ZIP sopra l'installazione esistente**, sovrascrivendo solo il codice. Non cancellare la cartella. Il pacchetto NON contiene `learning/`, `templates/`, `config.json`, salvataggi personali o screenshot; i preset scanner esistenti in SQLite vengono mantenuti.
