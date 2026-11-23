# Oye v25 — Web Control Room

**Windows 10/11 · Python 3.11+ · dashboard HTML/CSS/JavaScript con finestra WebView2 (`pywebview`) e fallback browser.**

## Prima apertura: un doppio clic

1. Estrai lo ZIP in una cartella normale.
2. Apri **`AVVIA_OYE.bat`**. Alla prima apertura può installare le librerie Python necessarie (connessione Internet richiesta). Se manca Python, installa Python 3.11 o superiore.
3. Si apre la dashboard web locale. La pagina usa un server **solo su `127.0.0.1`** con token temporaneo, senza account o hosting online.
4. Seleziona **Una partita** e premi **Avvia una partita**. Se è la prima volta, Oye ti porta alla **Calibrazione** per raccogliere i pulsanti e i riferimenti HUD essenziali. Questi riferimenti **non sono forniti pre-calibrati**, perché risoluzione, interfaccia e modalità di gioco possono variare.
5. Conferma l'invio degli input LIVE e porta Fortnite in primo piano. **F8** sospende; **F9** arresta in qualsiasi momento.

`apri_studio.bat` avvia direttamente il programma se le librerie sono già presenti; `apri_studio_browser.bat` forza l'apertura nel browser locale. `installa_windows.bat` installa manualmente le dipendenze. Il frontend Qt della v24 resta nei sorgenti (`studio_qt.py`), ma non è più l'interfaccia predefinita. L'editor timeline precedente rimane disponibile dalla pagina Impostazioni quando Oye è fermo (versione sorgente).

## Comandi standard

| Comando | Input |
| --- | --- |
| Avanti / indietro | W / S |
| Sinistra / destra | A / D |
| Salto | Spazio |
| Azione, colpo, piccone | Clic sinistro |
| Azione secondaria (solo quando richiesta) | Clic destro |
| Pausa / arresto | F8 / F9 |

Il piccone viene equipaggiato con il tasto definito in `config.json` (default F). Oye può eseguire un **clic destro breve circa ogni 26 secondi di gioco effettivamente riconosciuto**, mai durante attesa, bus, lobby o modalità spettatore. La dashboard indica i comandi previsti, mentre l'effettiva ricezione dal gioco richiede una verifica sul PC.

## Struttura dell'interfaccia

- **Panoramica**: avvio, pausa, stop, anteprima in tempo reale, riconoscimento OCR e stato degli input.
- **Calibrazione**: cattura programmata dopo cinque secondi o caricamento screenshot PNG/JPEG; selezione rettangolare e salvataggio dei riferimenti.
- **Impostazioni**: profilo, durata salti, intervallo tra i salti, rotazione telecamera, OCR e accesso all'editor timeline.

### Cosa Oye v25 non può garantire

Il nuovo frontend non modifica le restrizioni dell'input su Fortnite, non installa automaticamente il programma Tesseract e non aggira l'anti-cheat. La partita automatica richiede **pulsanti e HUD calibrati**, oltre alla conferma dell'input LIVE. I 46 test offline non sostituiscono un collaudo reale su Fortnite/Windows. L'automazione può essere vietata dalle regole del gioco.

## Test

```bat
py -m unittest discover -s tests -v
```

---

## Note precedenti (v24 e versioni precedenti)

# Oye Studio v24 — Una partita completa

**Windows 10/11 · Python 3.11+ · PySide6 (Qt) · Tesseract facoltativo**

## Nuova modalità: «Una partita»

1. Apri Fortnite e raggiungi la lobby, con il gioco in **primo piano**. Avvia `apri_studio.bat` e seleziona la modalità **Una partita** nella Panoramica.
2. Prima della prima esecuzione, apri **Calibrazione** e registra i pulsanti `gioca`, `continua`, `torna_lobby` (e preferibilmente `riscatta`), due riferimenti strutturali dell'HUD (`hud_salute`, minimappa o inventario) oppure `hud_in_partita`, e **entrambi** i controlli spettatore (`spettatore_prossimo`, `spettatore_precedente`). Puoi anche calibrare `bus_salto` per far partire prima i salti sul bus. Serve uno screenshot attendibile per ciascun riferimento; la selezione può essere verificata nella pagina Calibrazione. I pulsanti non sono codificati con coordinate fisse.
3. Imposta in **Impostazioni → Movimento** la durata iniziale dei salti (predefinita **120 s**, da 30 a 180). Il periodo tra i tentativi di salto è **1,8 s** e si può modificare nel `config.json` (`single_match_jump_interval_seconds`). Il profilo Equilibrato alterna piccoli spostamenti, deviazioni, osservazioni e piccone.
4. Premi **Avvia sessione**, conferma esplicitamente l'invio degli input LIVE e torna a Fortnite in primo piano. Puoi sospendere con **F8** e fermare con **F9**.
5. Oye tenta `GIOCA` **solo dopo conferma del pulsante su tre frame**; attende poi la scomparsa del pulsante e la conferma del gioco tramite HUD, o un riferimento calibrato del bus. **La sola sparizione della parola GIOCA non basta per premere tasti**: potrebbe essere ancora un caricamento.
6. Quando l'ingresso è verificato, parte una finestra di circa due minuti con pressione periodica di Spazio **intercalata** a movimenti e uso del piccone. Durante la presenza del solo prompt del bus può premere Spazio, ma non muoversi alla cieca. Finita la finestra, continua a esplorare finché non viene riconosciuta la schermata di fine partita.
7. Se compare **SPETTATORE / IN ATTESA DEL RIAVVIO**, sospende i comandi; riprende soltanto dopo conferma visiva del rientro. Alla fine usa `CONTINUA / RISCATTA / TORNA ALLA LOBBY` quando riconosciuti e, non appena rivede stabilmente la lobby, **termina la sessione, senza premere Gioca una seconda volta**. Se torna direttamente alla lobby, bastano cinque frame nuovi di conferma.

### Limiti operativi

- **Modalità guidata per una partita, non AI strategica.** I movimenti variano, ma non esiste una comprensione garantita della mappa o degli ostacoli; le correzioni usano euristiche di movimento. 
- **Riconoscimento obbligatorio:** senza template HUD e spettatore o pulsanti calibrati la modalità LIVE viene rifiutata, invece di indovinare coordinate. Tesseract riconosce gli stati spettatore/riavvio e alcuni pulsanti conclusivi, ma **non** sostituisce automaticamente la calibrazione di `GIOCA`.
- **Input del gioco non collaudati:** Windows/DirectX o le impostazioni del gioco possono ignorare mouse/tasti sintetici. Non sono inclusi bypass anti-cheat, hook di gioco o tecniche anti-rilevamento. L'automazione può violare le regole di Fortnite.
- Se il matchmaking non produce un ingresso verificato entro quattro minuti, o la schermata finale rimane bloccata per quattro minuti, Oye interrompe il ciclo e segnala l'errore. Non clicca a caso e non accoda nuove partite.
- Non sono stati effettuati test di gameplay in Fortnite in questo ambiente. I test offline verificano soltanto la logica e l'integrazione dei moduli eseguibili senza Qt.

### Prova offline

Dalla cartella del progetto:

```bat
py -m unittest discover -s tests -v
```

Per installare dipendenze Windows usa `installa_windows.bat`; la GUI è `apri_studio.bat`.

---

# Oye Studio v23 — Nuova interfaccia Qt

**Windows 10/11 · Python 3.11+ · PySide6 (Qt) · locale**

## Che cosa cambia

L'interfaccia è stata riscritta in **PySide6**, non è più Tkinter.
L'app si apre nella Panoramica e presenta quattro modalità distinte: **Osserva**, **Allenamento**, **Esplora e piccona** e **Pilota AI**.

- **Panoramica**: scelta modalità, avvio, pausa F8, arresto F9, anteprima del gioco e solo i cinque indicatori importanti.
- **Calibrazione**: apri/cattura un'immagine, trascina sul riferimento, salvalo; verifica del template con lo stesso formato di v22.
- **Partite e dati**: archivio, anteprima dei frame, correzione evento, insegnamento azione, annotazione per intervallo, esportazione JSONL e valutazione dataset. L'editor timeline completo di v22 rimane accessibile da qui.
- **Impostazioni**: OCR, Ollama, stile di movimento, routine a tempo, velocità acquisizione, dispositivo e test W/telecamera.

Il motore originale (`observer/`) resta invariato: OCR spettatore/riavvio, conferme di rientro, monitor LIVE, registrazione, policy e protezioni di input continuano a dipendere dal medesimo Runner. La nuova interfaccia **non modifica** il modo in cui Fortnite accetta o rifiuta gli input sintetici.

## Avvio

1. Estrai la cartella `Oye_v23` in una directory tua (non da dentro lo ZIP).
2. Se aggiorni dalla versione precedente, copia `learning/`, `templates/` e, se vuoi, `config.json` nella nuova cartella.
3. Avvia `installa_windows.bat` (richiede Internet per installare PySide6 e le altre dipendenze Python).
4. Per Tesseract, se non già configurato, installa il programma Windows e verifica con `prova_ocr_fasi.bat`. Il pacchetto `pytesseract` viene installato dallo script di setup.
5. Avvia `apri_studio.bat`.

**Importante:** la sessione parte sempre in modalità Osserva, con input disabilitati. La selezione della modalità LIVE non equivale all'autorizzazione: viene richiesta una conferma aggiuntiva. Prima di uscire, F9 ferma il Runner.

## Editor avanzato

La vecchia interfaccia è conservata in `studio_legacy.py` e `apri_editor_avanzato.bat`. Dalla pagina **Partite e dati** puoi aprirla soltanto quando non c'è una sessione in corso. Serve per funzioni particolari della timeline di v22 non ancora portate nella nuova GUI. Su una build EXE PyInstaller l'editor legacy richiede ancora Python installato e i sorgenti `observer/`: per usare l'editor completo avvia la versione sorgente con `apri_studio.bat`.

## Verifiche

Per i test:

```bat
py -m unittest discover -s tests -v
```

I test Qt vengono eseguiti solo quando PySide6 è effettivamente installato; gli altri sono indipendenti dall'interfaccia. **Non è stata effettuata una prova in Fortnite in questo ambiente.**

---

# Oye v22 — Activity Engine + OCR Reboot Guard

**Windows 10/11 · Python 3.11+ · applicazione locale**

Questa versione amplia **Esplora e piccona**: Oye non segue più una sola sequenza ripetitiva, ma alterna attività brevi, mantenendo il movimento mentre gira la visuale, devia, salta piccoli ostacoli e usa il piccone. È una routine locale con regole e variabilità limitata, **non** un'intelligenza capace di capire tutta la mappa.

## Nuovo: SPETTATORE e IN ATTESA DEL RIAVVIO con Tesseract

L'OCR è eseguito in un thread separato e analizza le zone centrali del fotogramma.
Riconosce `SPETTATORE`, `IN ATTESA DEL RIAVVIO` / `IN ATTESA DI RIAVVIO`,
`WAITING FOR REBOOT` e alcune varianti. Quando vede quei testi blocca i
comandi anche se il gioco mostra ancora l'HUD del compagno.

**Il messaggio di attesa non è un segnale per premere W!** Il personaggio è
ancora eliminato. Per riprendere la routine Oye aspetta 2 OCR consecutivi
senza l'avviso, 6 fotogrammi di scena partita e la conferma dell'HUD da parte
del cervello originale. A quel punto fa una breve prova W di 0,45 secondi,
controlla la variazione visiva e ritorna all'esplorazione. In modalità a tempo
questa ripresa non ricomincia i 90 secondi di caricamento.

L'OCR funziona solo se sono installati **sia** il programma Tesseract OCR **sia**
il pacchetto Python `pytesseract`. Usa `installa_ocr_windows.bat`, poi
`prova_ocr_fasi.bat` con uno screenshot locale per verificare che il testo
sia leggibile. L'elaborazione è in RAM; le immagini dei turni autonomi non
vengono salvate. In caso di errore OCR il programma lo segnala e non usa
l'errore per sbloccare automaticamente la fase di attesa.

Per riconoscere con sicurezza il personaggio **dopo** il riavvio, calibra
le cornici HUD (salute/inventario/minimappa) e i riferimenti dello spettatore.
Il solo testo non prova che il personaggio sia controllabile. In mancanza
resterà in attesa. La routine a tempo di v21 resta una stima e non equivale
al riconoscimento della partita.

## Installazione e aggiornamento

1. Estrai lo ZIP in una **cartella nuova**.
2. Dalla versione precedente conserva una copia di `learning/` e `templates/` e copiale nella nuova cartella. Se vuoi mantenere anche le impostazioni, puoi copiare `config.json`: i nuovi campi vengono completati con valori predefiniti.
3. Avvia `installa_windows.bat`, poi `apri_studio.bat`.
4. Nella **Panoramica** scegli lo stile, poi avvia **03 Esplora e piccona**.
5. F8 = pausa, F9 = arresto. Gli input vengono rilasciati quando Oye si ferma o la finestra del gioco perde il primo piano.

Non è necessario avere Ollama per la routine semplice.

## Tre stili di esplorazione

| Stile | Comportamento |
| --- | --- |
| **Equilibrato** | Alterna avanzamento, osservazione, cambi di direzione, piccoli salti e piccone |
| **Esploratore** | Più correzioni del percorso e cambi di visuale, meno piccone |
| **Raccoglitore** | Più periodi di piccone, mantenendo comunque la camminata |

Le attività sono **Osserva i dintorni**, **Esplora una direzione**, **Corregge il percorso**, **Supera piccoli dislivelli** e **Usa il piccone**. Ogni attività genera una sequenza di azioni di durata limitata. Oye non clicca a caso sui menu e non tenta combattimenti autonomi nella routine semplice.

Il sistema conserva le protezioni precedenti: stop immediato, focus della finestra, controlli dei menu, diagnostica degli input, tentativi limitati di recupero quando l'avanzamento non è verificato.

## Interfaccia

La dashboard scura contiene **Monitor LIVE**, anteprima del fotogramma, **Attività in corso**, profilo selezionato e **Attività recenti**. Nella Panoramica puoi scegliere lo stile e usare **Anteprima routine** per vedere le prossime 20 azioni in una finestra separata **senza inviare input al gioco**. Puoi anche eseguire `anteprima_routine.bat` oppure:

```bat
py -3 anteprima_routine.py --profilo esploratore --azioni 25
```

L'anteprima usa una sequenza riproducibile: in una partita reale, gli ostacoli, i suggerimenti ML e i controlli di sicurezza possono cambiarla.

## Come vengono trattati gli screenshot

- **Quando giochi tu**: gli screenshot e le dimostrazioni possono essere conservati in `learning/` per la revisione.
- **Quando gioca Oye**: gli screenshot LIVE vengono acquisiti e analizzati **in RAM**, ma non aggiunti all'archivio immagini delle partite autonome.

L'editor time-lapse, la timeline IN/OUT e la correzione multipla delle tue partite restano disponibili.

## Limitazioni da conoscere

- Le rotazioni usano l'input standard del mouse; **Fortnite potrebbe ignorarle** se usa input raw o filtra gli input sintetici. Usa **Diagnostica: prova rotazione mouse** per verificarlo sul tuo PC.
- La modalità a tempo può stimare male caricamento, bus e atterraggio. La barra salute da sola non dimostra che il personaggio sia a terra.
- Le variazioni visive non dimostrano sempre che W abbia prodotto un avanzamento: animazioni e rotazioni possono alterare i fotogrammi.
- L'Activity Engine non rileva automaticamente bersagli, chest o percorsi ottimali. Non è progettato per aggirare l'anti-cheat o simulare una persona ai fini dell'elusione dei controlli del gioco.
- L'automazione può essere incompatibile con le regole del gioco e comportare sanzioni. Verifica le condizioni d'uso prima di attivare LIVE.

## Verifiche

La suite automatica copre planner, profili, input combinati, stop, focus, test HUD, registrazione, timeline, OCR e regressioni delle versioni precedenti. Questi test sono **offline** e non costituiscono un collaudo di Fortnite su Windows.
