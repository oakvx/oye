# Oye v34 — Avvio rapido realmente temporizzato

## Cosa fa, in ordine

1. **L'utente** apre Fortnite e preme **Pronto** o avvia il matchmaking. Poi seleziona **Avvio rapido** in Oye e conferma l'invio dei comandi LIVE. L'avvio di questa modalità è una richiesta esplicita di una routine **a tempo**, non una dimostrazione che la partita sia iniziata.
2. Oye arma un cronometro di **20 secondi effettivi**, conteggiati solo quando ha una nuova acquisizione e la finestra di Fortnite rimane in primo piano. **F8/Pausa** e perdita di focus interrompono il conto. **F9/Stop** ferma tutto.
3. Se vede in modo affidabile **Gioca**, tenta prima il clic e ricomincia il conto. Se Gioca non è calibrato, Oye non resta bloccato: il cronometro è già armato dall'utente.
4. Se la scena è sconosciuta, al termine del timer Oye prova **Spazio** senza bisogno del bus, della barra salute o di Tesseract. Se è ancora riconosciuto un caricamento, concede fino a **15 secondi** aggiuntivi, poi la routine temporizzata può partire anche senza un segnale HUD. I menu riconosciuti di lobby/risultati/spettatore bloccano gli input.
5. Nella finestra iniziale Oye preme **Spazio periodicamente** secondo l'intervallo impostato (predefinito 1,8 s) e, tra i salti, prova un ciclo deterministico di **W/A/D, rotazioni, una selezione del piccone e clic sinistro con W**. Non dipende dalla riuscita di alcuna previsione ML. I comandi rimangono corti e interrompibili.
6. Dopo i 120 secondi iniziali prosegue con l'esplorazione. Per le ricompense rimangono prioritari gli stati OCR e i pulsanti che l'utente gli ha insegnato: **Ricompensa ottenuta → Riscatta → lobby confermata → Gioca**. `Equipaggia` non è scelto dal ciclo.
7. Se manca Tesseract, il timer può funzionare; **non può però riconoscere i testi spettatore/riavvio e la verifica ricompense può rimanere in attesa**. Oye deve mostrare l'avviso. Se manca del tutto uno screenshot fresco, non agisce: non può nemmeno escludere un menu.

## Input Windows

I tasti vengono inviati con i normali metodi pubblici di PyAutoGUI: **W/A/S/D, Spazio, clic sinistro/destro**. Per selezionare il piccone usa il tasto impostato in **Impostazioni → Tasto selezione piccone**, inizialmente `F` per compatibilità con la configurazione precedente. Se Fortnite ha un altro tasto, devi cambiarlo. L'invio dell'input al sistema **non garantisce** che Fortnite lo recepisca. Le restrizioni del gioco non vengono aggirate.

## Se Oye non si muove

- Se la dashboard indica **Avvio rapido: N s al timer**, il cronometro è attivo e non ha ancora raggiunto la soglia.
- Se indica **Fortnite non verificato in primo piano**, rimetti il gioco davanti alla finestra di Oye.
- Se indica **OCR spettatore/riavvio**, la routine rimane sospesa fino a verifica del rientro.
- Se indica **Driver: comando non inviato**, controlla i permessi Windows e la finestra autorizzata.
- Se indica **Driver: comando inviato**, ma il gioco non si muove, va verificata sul PC la ricezione degli input: i test automatici non possono dimostrarla.
- Se non arrivano fotogrammi, correggi prima la cattura. Un timer senza alcuna immagine non distingue una partita da un menu.

## Aggiornamento senza perdere i vecchi training

Lo ZIP non contiene `learning/`, `templates/`, `state/`, `logs/`, `config.json` né i salvataggi. Chiudi Oye, fai una copia preventiva della cartella, estrai lo ZIP **nella cartella esistente** e sovrascrivi soltanto i file di programma. `AVVIA_OYE.bat` prepara l'ambiente e conserva i tuoi dati. Gli archivi personali SQLite e gli screenshot non sono sostituiti dal pacchetto.

## Verifiche

Le prove offline coprono: timer armato manualmente senza Gioca/HUD/OCR, temporizzazione con pausa, lobby/spettatore/risultati, tasti W/A/D/Space, piccone e clic sinistro con rilascio, impostazioni piccone, e aggiornamento ZIP che non tocca i training. **Non** coprono una partita reale né garantiscono la ricezione di input sintetici nel gioco.
