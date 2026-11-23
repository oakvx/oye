# Oye v40 — Scanner visivi modulari e riconoscimento degli stati

## Scopo

La v40 introduce scanner indipendenti che analizzano regioni (ROI) **dello stesso screenshot**, invece di confrontare una schermata Fortnite intera con un'immagine immutabile. Gli scanner strutturali sono **euristici** e devono essere calibrati con screenshot veri: i test sintetici non dimostrano accuratezza sul gioco.

## Avvio e migrazione

Chiudi Oye e crea una copia della cartella esistente. Estrai lo ZIP nella cartella di Oye che contiene `AVVIA_OYE.bat`, sovrascrivendo i file di programma. Non cancellare prima la cartella: database, screenshot, template e profili vengono lasciati al loro posto. Le nuove tabelle SQLite (`oye_v40_scanners`, `oye_v40_scanner_examples`) sono aggiunte in modo non distruttivo al database `learning/observer.sqlite3`. I preset vengono creati **disabilitati** finché non li configuri e li testi con immagini reali; nulla viene classificato come certo sulla base di un preset non verificato.

## Configurazione in Web Studio

1. Apri Oye Mini → Studio → **Scanner modulari** (`/scanners`).
2. Apri Osserva o carica uno screenshot, seleziona uno scanner o creane uno, e disegna la ROI sullo screenshot. I quattro numeri [x1,y1,x2,y2] vengono salvati normalizzati tra 0 e 1, quindi il riquadro si ridimensiona con la risoluzione. Una diversa scala HUD può comunque richiedere una nuova calibrazione.
3. Scegli metodo: `ocr`, `structure`, `template`, `features`, `color`, oppure `model`. La modalità `model` restituisce **UNKNOWN** se non esiste un backend di inferenza configurato; non promette un modello visivo funzionante.
4. Configura soglia, peso, intervallo, stato, testo atteso e opzioni. Premi **Prova** e leggi punteggio/diagnostica; solo quando ottieni risultati soddisfacenti abilita lo scanner e salva.
5. Per correggere un caso salva la ROI con etichetta **positiva** o **negativa**; gli esempi sono revisionabili, valutabili ed eliminabili. Per template e feature matching un'immagine positiva può essere usata come riferimento; la correzione da sola **non riaddestra** un modello.
6. Usa Esporta/Importa per trasferire i preset e ripristinare i valori di default. **Il reset richiede una nuova calibrazione** dei riferimenti in base alla schermata reale.

### Preset inclusi

`compass`, `minimap`, `health_shield`, `inventory`, `player_counter`, `elimination_counter`, `lobby`, `matchmaking`, `loading`, `battle_bus`, `skydiving`, `gliding`, `elimination`, `spectating`, `return_to_lobby`, `reward_title` e relativi riferimenti disponibili nell'editor. Numeri dinamici non sono criteri obbligatori dello stato. I metodi struttura stimano forme, bordi e bande, con falsi positivi possibili.

## Motore e logica

Il worker `ScreenRegionOCRWorker` riceve **una copia del frame già acquisito dal Runner** e la distribuisce agli scanner. OCR è elaborato asincronamente in modo limitato per ciclo; i lettori CV operano alle frequenze configurate. Ogni osservazione ha stato `present`, `absent`, `unknown` o `error`; **assenza di una risposta OCR NON è assenza del pulsante**. Le osservazioni scadono indipendentemente. La fusione `StateRecognitionEngine` combina evidenze parziali, attenua segnali correlati, applica priorità su lobby/eliminazione e stabilizza i cambi con una finestra temporale. Per segnali insufficienti o contraddittori restituisce `UNKNOWN`. I punteggi non sono probabilità statisticamente calibrate.

Il Runner riutilizza la classificazione modulare come indicazione per il controller esistente senza sostituire i meccanismi legacy di sicurezza. Le azioni restano vincolate alla conferma LIVE, al focus e allo Stop/F9; gli eventi di input sono tracciati, ma non è possibile garantire che Fortnite li abbia ricevuti senza verifica dal gioco. La modalità Ignorante mantiene la sequenza temporizzata configurata nel progetto: l'aggiunta degli scanner non cambia arbitrariamente la sua logica.

## Debugger

La pagina Scanner modulari mostra frame, ROI sovrapposte, ingrandimento dell'ultima ROI, confronto con la ROI precedente, diagnosi e tempi per scanner, stato riconosciuto, alternative, motivazione e ultima transizione. CPU/RAM richiedono `psutil` (incluso nelle dipendenze v40); il pannello mostra `—` se la misura non è disponibile. Il polling della dashboard non è il tempo di cattura.

## Replay offline

```bash
python replay_scanners.py --folder /percorso/ai/fotogrammi --root . --output replay-scanners-v40.jsonl
python replay_scanners.py --manifest frames.json --root . --output replay-scanners-v40.jsonl
```

Il manifest è un array JSON del tipo `[ {"file":"001.png","timestamp":1.0}, {"file":"002.png","timestamp":1.4} ]`. La CLI non invia input, non apre Fortnite, usa gli scanner salvati nella cartella `--root` e restituisce le decisioni per frame come JSONL. In assenza di dataset reali, i test della suite sono su fixture sintetiche.

## Test eseguibili

```bash
python -m unittest discover -s tests -q
python studio.py --smoke-test
node --check web/scanners.js
```

`studio.py --smoke-test` verifica il server HTTP locale e l'API protetta; il test completo suite include anche richieste HTTP dello scanner, SQLite, fusion, dataset, replay e compatibilità con i test legacy. Un `.exe` Windows non è incluso nello ZIP sorgente. Il test live su Fortnite, la compatibilità con overlay, HUD diversi, risoluzioni Windows e la ricezione effettiva dei tasti **rimangono da verificare**.

## Aggiornamento v41

Per gli interventi successivi vedi `docs/OYE_V41_FIXES.md`: riconoscimento prudenziale, timeout, scheduler e test corretti.

## Limiti residui

- I rilevatori geometrici di compass/minimap/salute/inventario sono euristiche iniziali: vanno calibrati e valutati su screenshot acquisiti nel proprio setup 1920 × 1080 borderless, non garantiscono riconoscimento automatico universale.
- `model` è un'estensione predisposta, non un modello già addestrato o integrato.
- Lo scheduling limita gli OCR ma latenza e prestazioni dipendono dall'hardware; `UNKNOWN` può essere frequente finché non si configurano scanner fortemente discriminanti.
- Le spiegazioni libere delle schermate non diventano automaticamente codice eseguibile. Le azioni operative rimangono quelle previste dal controller.
- Lo ZIP contiene soltanto sorgenti/risorse, non l'installer compilato. I collaudi Windows/Fortnite restano necessari.
