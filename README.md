# Oye 44.2.0

## Applicazione Windows e modello visivo incluso

La build locale si avvia da `dist/OyeStudio/OyeStudio.exe`, senza installare Python.
Il pacchetto portatile è `release/Oye-Windows-pretrained.zip`: estrarre tutta la
cartella prima di aprire l'eseguibile. Impostazioni e registrazioni della build
compilata sono conservate in `%LOCALAPPDATA%/Oye`.

Il riconoscitore visivo incluso è già addestrato: 72 esempi selezionati da 2.810
screenshot, più 24 immagini separate per la verifica. Su queste ultime riconosce
21 immagini correttamente e si astiene su 3. Le annotazioni derivano da revisione
visiva AI; la verifica non separa partite indipendenti e non dimostra affidabilità
nel gioco reale. Il modello apprende quali zone del layout distinguono le fasi:
usa posizione e densità dei componenti testuali di HUD e menu, esclude il centro
della scena e l'interno della minimappa, normalizza la risoluzione e il contrasto.
Non confronta gli screenshot pixel per pixel. I pesi delle zone e la soglia sono
appresi solo dai 72 esempi di training e salvati nel pacchetto, senza riaddestrare
all'avvio. È un classificatore per esempi di layout, non una rete neurale.

Verifica delle variazioni sulle 24 immagini escluse dal training: 21 corrette
con scenario centrale sostituito, 22 con luminosità al 65%, 21 a 1280×720 e 21
con traslazione di due pixel; le restanti sono astensioni. Queste prove non
coprono tutte le skin, lingue, proporzioni dello schermo o modifiche future dell'HUD.

Il modello viene caricato automaticamente come supporto quando mancano altre
evidenze. Non sostituisce OCR, riferimenti HUD o correzioni personali; non contiene
mosse apprese. Gli screenshot disponibili non registrano i tasti premuti, quindi
il training delle azioni richiede ancora dimostrazioni con comandi associati.

Report e provenienza: [assets/pretrained/visual.json](assets/pretrained/visual.json)
e [manifest](assets/pretrained/manifest.json). Gli originali restano locali.
Per riprodurre il training con gli screenshot originali:

```powershell
python tools/train_visual.py --manifest assets/pretrained/manifest.json --images frames
python -m pytest -q
python studio.py --smoke-test
```

Il modello numerico è incluso dal comando di build esistente tramite `assets`.
Tesseract OCR rimane una dipendenza Windows separata (`installa_ocr_windows.bat`).

Oye è un'app Python per Windows con una modalità sperimentale **Ignorante**: osserva schermate e può inviare input autorizzati per un ciclo di partita semplice. Non è verificato che Fortnite accetti i comandi o che il riconoscimento sia affidabile nel gioco reale.

L'automazione può violare le regole di Epic e comportare sanzioni: controlla le regole vigenti; eventuali test reali solo con un account secondario che sei disposto a perdere. Non sono previste tecniche di elusione dell'anti-cheat. F8 mette in pausa, F9 arresta, e Fortnite deve rimanere in primo piano.

- Avvio Windows e installazione: [LEGGIMI.md](LEGGIMI.md)
- Diagnostica e limiti Ignorante: [docs/IGNORANTE_DIAGNOSTICA.md](docs/IGNORANTE_DIAGNOSTICA.md)
- Protezione e backup dati: [docs/REPOSITORY_DATI_LOCALI.md](docs/REPOSITORY_DATI_LOCALI.md)
- Versione: [VERSION.txt](VERSION.txt)

La CI verifica test e build, ma non esegue partite Fortnite. I risultati su dati simulati non sono una validazione nel gioco.
