# Oye v42 — Profilo ricavato da `frames.zip`

## Provenienza e limiti dei dati

- Fonte: `frames.zip`, **136 screenshot JPEG**, tutti **1920 × 1080**.
- Nel file ZIP le date delle immagini vanno dal **22 novembre 2026, 16:13:12** al **16:28:14**. Le immagini **000–134** mostrano principalmente **una partita**, registrata per circa sette minuti; la **135** mostra una lobby otto minuti dopo. **Non ci sono tre partite complete indipendenti**.
- I nomi dei file sono casuali; l'ordine per il replay viene ricostruito dalla data di modifica memorizzata nello ZIP. Le singole immagini non hanno EXIF.
- Gli esempi sono ottenuti da una **sola sessione**. I risultati di replay non sono una validazione indipendente e non misurano la ricezione degli input Windows.

## Regola più importante: Gioca non è nella barra superiore

L'etichetta `GIOCA` nella barra superiore è una tab e **rimane visibile anche durante il matchmaking**. Non è un criterio per stabilire se la ricerca è iniziata.

Il pulsante operativo è in basso a sinistra. Nella sequenza fornita cambia da **GIOCA** a **ANNULLA**; un'immagine illeggibile non viene interpretata come assenza del pulsante. Solo più scansioni reali possono confermare il cambio.

## Sei scanner già preparati

| Scanner | Regione pixel su 1920×1080 | Segnale | Stato che supporta |
|---|---|---|---|
| `lobby` | (65,830)–(460,990) | Testo grande **GIOCA** a sinistra | LOBBY |
| `matchmaking` | (65,830)–(460,990) | **ANNULLA** nella stessa posizione | MATCHMAKING |
| `spectating` | (730,0)–(1280,115) | **SPETTATORE** in alto | SPECTATING |
| `reboot_wait` | (730,540)–(1300,730) | **IN ATTESA DEL RIAVVIO** | SPECTATING |
| `elimination` | (680,0)–(1250,143) | **POSIZIONE FINALE** | RETURNING_TO_LOBBY |
| `return_to_lobby` | (1230,380)–(1890,690) | **RITORNA ALLA LOBBY**, separato da Gioca di nuovo | RETURNING_TO_LOBBY |

Il confronto è limitato alle regioni descritte. I campioni `.png` distribuiti in `assets/fortnite_1920x1080_v42/` contengono **ritagli del testo**, non screenshot completi. Gli scanner usano lo stesso frame condiviso con il motore v41 e sono configurati in SQLite al primo avvio standard.

Le sei zone possono essere disattivate o modificate da Studio > Scanner modulari; **una personalizzazione precedente non viene sovrascritta**. I template nuovi sono subordinati agli esempi insegnati personalmente dall'utente.

**Non attivati automaticamente**: bussola, minimappa, vita/scudo, inventario, bus, deltaplano. Le immagini mostrano variazioni e, dopo l'eliminazione, un HUD quasi identico a quello di un personaggio controllabile. Sarebbe rischioso usare questi elementi come autorizzazione sufficiente agli input.

## Che cosa è integrato

- Nel progetto sorgente, il primo avvio con `AVVIA_OYE.bat` inizializza il profilo 1920×1080 senza bordi **soltanto se non ne esiste già uno selezionato**.
- `ScannerStore` registra le sei ROI in SQLite senza toccare altri scanner già personalizzati e senza riscrivere `frames/` o `observer.sqlite3`.
- Il worker visivo riceve un solo frame e prova sei regioni a frequenze configurate; i template non richiedono Tesseract. Gli OCR personali rimangono disponibili.
- Il Runner continua a usare la propria macchina a stati, senza una seconda automazione parallela. `UNKNOWN` resta prudenziale per le parti che non sappiamo identificare.
- Il timeout della memoria impedisce che una vecchia schermata di risultati venga portata oltre un lungo intervallo senza frame.
- Per verificare sullo ZIP originale, eseguire `python verify_real_shots.py frames.zip --output report.json` dalla cartella del progetto. È un replay **senza input**; verifica il checksum del file originale, perché le etichette sono specifiche per queste 136 immagini.

## Risultati del replay (stessa sessione, non holdout indipendente)

Vedere `reports/oye42_screenshot_validation.json` per la matrice completa. In questo replay, **64/65 frame etichettati spettatore** risultano riconosciuti come `SPECTATING`. Alcuni cambi di fase conservano la classificazione precedente per pochi secondi a causa dell'isteresi; i comandi non devono essere autorizzati da risultati che non hanno il candidato corrente confermato.

Gli screenshot di gioco non vengono ancora promossi automaticamente a `IN_MATCH`, perché gli elementi HUD mostrati sono presenti anche mentre si osserva un compagno. Per bus/atterraggio e ricompense saranno necessari esempi aggiuntivi; **nel materiale fornito non compare un pulsante RISCATTA**.

## Da testare davvero su Windows/Fortnite

1. Screenshot catturato dalla finestra corretta, pixel 1920×1080 senza ridimensionamento DPI.
2. Il pulsante sinistro cambia realmente da Gioca ad Annulla, poi sparisce in caricamento.
3. I controlli Stop/Pausa e la rotazione effettiva della telecamera.
4. Clic nel pulsante Ritorna alla lobby con focus corretto e verifica di successo.
5. Eventuale schermata Ricompensa ottenuta / Riscatta non presente nel campione.

Non dichiarare la funzionalità di una partita LIVE validata da questa raccolta offline.
