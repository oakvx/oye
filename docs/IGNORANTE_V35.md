# Modalità Ignorante v35 — Contratto operativo

## Sequenza

1. L'utente seleziona **Ignorante** nella dashboard, conferma gli input LIVE e preme Avvia. Non sono necessari riferimenti visivi.
2. Il motore si arma immediatamente ma **non invia azioni prima di 20 secondi effettivi** con Fortnite in primo piano. Il tempo trascorso quando la finestra non è attiva o con F8 in pausa non conta.
3. Dopo il countdown: primo `Space` (salto). Segue una sequenza deterministica: selezione piccone (una sola volta all'inizio), W, W+clic sinistro, W+A, W+rotazione, W+clic sinistro, W+D, W+salto e W+rotazione opposta. `Space` viene ripetuto durante la fase iniziale (fino a 120 secondi), con intervallo di circa 1,8 secondi tra le richieste.
4. Dopo la finestra iniziale continua con WASD e piccone. La sequenza non richiede screen capture, Tesseract o HUD riconosciuto. Anche se Tesseract è assente o SQLite non si apre, il motore usa la routine deterministica e non modifica il database problematico.
5. Se screenshot e OCR funzionano, possono individuare menu finali: un'indicazione credibile di spettatore/risultati/ricompense prevale sui comandi di gioco. Se non c'è alcuna immagine, non vengono mai inventati clic su Riscatta o Gioca.
6. Solo **Stop/F9**, **Pausa/F8**, un input driver realmente fallito o perdita della finestra Fortnite attiva possono impedire l'invio dei comandi. Con un errore di acquisizione si mantiene il timer e si mostra esplicitamente il problema in telemetria.

## Limiti

Questa modalità non può verificare che Fortnite abbia ricevuto i tasti; `comando trasmesso` indica solo la chiamata al driver. In assenza di OCR non può garantire l'uscita dalle ricompense o la ripartenza automatica di un'altra partita. Nessun bypass anti-cheat è incluso.

## Conservazione dei dati

Lo ZIP aggiornamento esclude `config.json`, `learning/`, `templates/`, `state/`, `logs/`, `reports/`. Non eliminare la cartella esistente: estrai sopra.

## Aggiornamento v36: comportamento da principiante

Dopo i primi passi di diagnostica la routine non ripete più un ciclo fisso di dieci comandi. Alterna brevi attività di esplorazione, curiosità, cambi di direzione e picconate; comprende anche piccoli passi indietro, rotazioni isolate della camera, accovacciate e clic destro occasionali. I movimenti restano brevi e interrompibili.

Contatori interni impongono movimento dopo al massimo due azioni non locomotorie, un salto entro dieci decisioni e un'azione di piccone entro sette decisioni (se abilitato). Dopo i 120 secondi iniziali continua a saltare occasionalmente anche in assenza di screenshot. I comandi effettivi dipendono comunque dal focus di Fortnite e dal fatto che il gioco accetti input simulati.

La varietà di movimento **non** garantisce né mira a eludere riconoscimenti dell'automazione, anti-cheat o segnalazioni da parte degli altri giocatori. Preferire modalità private e rispettare le regole della piattaforma.
