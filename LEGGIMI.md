Leggi [CAMBIAMENTI_V44_2.md](CAMBIAMENTI_V44_2.md) per il controllo uniforme di Gioca, Torna alla lobby, Continua, Riscatta e Gioca di nuovo.

# Oye v44.1 — Correzione clic GIOCA in lobby

**Bug corretto:** Oye poteva riconoscere GIOCA e mostrare «Vedo GIOCA», ma restare fermo perché il componente Brain aveva proposto `wait` invece di `menu_click`. In Ignorante ora il controller, dopo tre nuove letture valide, può usare la posizione del pulsante rilevata sullo schermo per inviare il clic. Dopo il clic verifica il cambio della schermata; se GIOCA rimane visibile, tenta al massimo tre clic complessivi, separati da 12 secondi, poi segnala il problema. Non clicca coordinate inventate e non tenta il clic quando manca il focus di Fortnite, quando il target non è confermato o quando la posizione non è disponibile.

**Dalle foto fornite:** la dashboard conferma «Vedo GIOCA: fermo in lobby» e il gioco mostra il pulsante giallo GIOCA con «Non pronto». È un problema di collegamento riconoscimento → input, non necessariamente dell'OCR.

**Per aggiornare:** chiudi Oye, fai un backup, estrai questo ZIP sulla stessa cartella. Non contiene `learning/`, `templates/`, `state/` o `config.json` personali. Avvia `AVVIA_OYE.bat`, seleziona Ignorante, conferma e torna sulla finestra Fortnite. Leggi sotto **Input inviato** e **Telemetria** per vedere se il clic è stato inviato; la ricezione del gioco deve essere verificata sul PC.

**Limiti:** il test automatico prova il controller e il driver simulato, non dimostra che Fortnite accetti effettivamente il clic sintetico; il focus è obbligatorio. Se il gioco resta su GIOCA dopo tre clic inviati, controlla la finestra in primo piano e la ROI del pulsante.

---

# Oye v44 — Miglioramento del training

**Nuova modalità guidata per insegnare le mosse.** Da Oye Mini premi **Insegna le mosse**; nello Studio premi **Registra le mie mosse → Termina registrazione → Rivedi le mosse**. Verifica ogni esempio incerto con il suo screenshot, correggi l'azione o scartalo. Scartare non cancella lo screenshot: impedisce soltanto che la policy lo utilizzi. La nuova revisione sostituisce la vecchia approvazione in blocco nella dashboard.

L'aggiornamento ZIP non contiene i tuoi dati (`learning/`, `templates/`, `config.json`): chiudi Oye, fai una copia di sicurezza e **estrai i file sopra la cartella esistente**. Apri `AVVIA_OYE.bat` come prima. Le vecchie dimostrazioni rimangono valide e le nuove colonne SQLite sono aggiunte senza cancellazioni.

Leggi [CAMBIAMENTI_V44.md](CAMBIAMENTI_V44.md) per il dettaglio di file, migrazioni, API e limiti. Il training non garantisce una partita autonoma funzionante: la ricezione degli input da parte di Fortnite va ancora verificata su Windows.

---

# Oye v43 — Rilevamenti OCR con contatore e storico

**Novità v43:** in Oye Mini e in Studio → Scanner modulari compaiono i rilevamenti OCR confermati, il totale storico, il numero di letture e gli elementi individuati. Anche i lettori OCR per Spettatore, Riavvio e Ricompense contribuiscono ai conteggi. Le scritte rilevate generano anche un evento «OCR trovato: …» nel registro. Una scritta visibile per più fotogrammi viene contata una sola volta; una nuova comparsa dopo assenza confermata genera un nuovo evento. Il totale storico è conservato in `learning/observer.sqlite3` tramite le nuove tabelle `oye_ocr_detection_counts` e `oye_ocr_detection_events`. Le letture non riuscite non sono considerate assenza.

**Nota:** gli OCR a zone vengono eseguiti in modo continuativo mentre Oye osserva, secondo lo scheduler già esistente; non viene lanciato un OCR completo a ogni frame. Questa versione non modifica il motore di input o il ciclo automatico di partita.

## Base della v42

# Oye v42 — Profilo reale Fortnite 1920×1080

Questa versione si basa su **136 screenshot reali** forniti dall'utente, confrontati come una registrazione cronologica. Si tratta soprattutto di una sola partita; non sono tre partite indipendenti.

## Come aggiornare senza perdere i dati

1. **Chiudi Oye e crea una copia di sicurezza della cartella attuale.**
2. Estrai lo ZIP v43 **direttamente nella cartella esistente** che contiene `AVVIA_OYE.bat`, sovrascrivendo i file del programma.
3. Non cancellare le cartelle `learning/`, `templates/`, `state/`, né `config.json`.
4. Fai doppio clic su `AVVIA_OYE.bat`. Potrebbero servire Python e dipendenze per il primo avvio, come nelle versioni precedenti.
5. Apri **Oye Mini**. Le sei nuove ROI calibrate sono già disponibili nello Studio; i tuoi riferimenti personalizzati hanno la precedenza.

Gli scanner preparati **non pretendono che bussola, minimappa o salute siano identiche** in ogni screenshot. Il profilo non riconosce ancora in modo affidabile TUTTE le fasi, e non garantisce che Fortnite riceva gli input.

## Materiale incluso

- `assets/fortnite_1920x1080_v42/`: piccoli ritagli visivi per le sei zone specifiche.
- `observer/v42_profile.py`: risoluzione, coordinate ROI normalizzate e soglie.
- `verify_real_shots.py`: replay della raccolta originale **senza input**, con checksum.
- `reports/oye42_screenshot_validation.json`: esiti offline per ciascuna fase.
- `docs/OYE_V42_SCREENSHOT_PROFILE.md`: dettagli, ROI e limitazioni.

Il pacchetto **non include i 136 screenshot originali, il tuo database o la configurazione personale**. L'archivio è un aggiornamento sorgente con avvio assistito, non un `.exe` compilato e collaudato su Windows.
