<p align="center">
  <img src="assets/pirate-token.svg" width="200" alt="Logo OYE: token dorato" />
</p>

<h1 align="center">OYE</h1>
<p align="center"><strong>LA TUA LOBBY. IL TUO LOADOUT. IL TUO STUDIO.</strong></p>
<p align="center">Osservazione dello schermo, routine sperimentali e training visivo per Fortnite su Windows.</p>

<p align="center">
  <img src="https://img.shields.io/badge/VERSIONE-44.2.0-9333EA?style=for-the-badge&amp;labelColor=151926" alt="Versione 44.2.0" />
  <img src="https://img.shields.io/badge/PIATTAFORMA-WINDOWS-1E90FF?style=for-the-badge&amp;labelColor=151926" alt="Windows" />
  <img src="https://img.shields.io/badge/STATO-SPERIMENTALE-FFD60A?style=for-the-badge&amp;labelColor=151926" alt="Sperimentale" />
</p>

<p align="center">
  <a href="#drop-in">Avvio rapido</a> ·
  <a href="#loadout">Funzionalità</a> ·
  <a href="#comandi">Comandi</a> ·
  <a href="#officina">Sviluppo</a>
</p>

<p align="center"><img src="assets/rarity-divider.svg" width="100%" alt="" /></p>

## Benvenuto in lobby

**OYE** è uno studio per osservare le schermate di Fortnite, riconoscere elementi dell'interfaccia e provare routine di tastiera e mouse. La modalità **Ignorante** collega il riconoscimento dei menu a movimenti semplici; lo **Studio** permette di registrare, rivedere e correggere dimostrazioni.

Il **token dorato è il logo del progetto**: identifica OYE e non rappresenta una ricompensa ottenibile con il programma. OYE non garantisce token, accesso allo Shop o premi di gioco.

## Loadout

| Slot | Equipaggiamento | A cosa serve |
| :--- | :--- | :--- |
| 🔵 Raro | Scanner OCR | Leggere pulsanti e segnali nelle zone configurate dello schermo. |
| 🔵 Raro | Riconoscitore visivo | Supportare il riconoscimento delle fasi attraverso il layout dell'HUD. |
| 🟣 Epico | Modalità Ignorante | Provare il ciclo dei menu e routine semplici di movimento, salto e piccone. |
| 🟣 Epico | Studio | Registrare le mosse, rivedere gli screenshot e correggere o scartare esempi. |
| 🟡 Leggendario | Replay offline | Analizzare screenshot locali senza inviare comandi al gioco. |

Le rarità sono una scelta grafica, non una misura dell'affidabilità.

## Drop in

### Avvia la build Windows

1. Su GitHub apri **Actions → Build Windows EXE** e scegli una run completata con successo.
2. Scarica uno degli artefatti disponibili:
   - **OyeStudio-windows-x64**: estrai tutta la cartella e avvia `OyeStudio.exe`.
   - **Oye-Setup-windows-x64**: estrai e avvia `Oye-v44-Setup-Windows-x64.exe`.
3. Installa **Tesseract OCR** per la lettura del testo. Nel checkout sorgente trovi `installa_ocr_windows.bat`.

La build compilata non richiede Python. I suoi dati personali vengono conservati in `%LOCALAPPDATA%/Oye`.

### Parti dai sorgenti

Con Python installato su Windows, esegui dalla cartella del progetto:

```bat
installa_windows.bat
installa_ocr_windows.bat
AVVIA_OYE.bat
```

## Comandi

| Comando | Effetto |
| :--- | :--- |
| **F8** | Mette in pausa la routine. |
| **F9** | Arresta la routine. |
| **Insegna le mosse** | Apre il percorso di registrazione e revisione delle dimostrazioni. |

Per provare Ignorante, seleziona la modalità nell'app e riporta Fortnite in primo piano. Controlla **Input inviato** e **Telemetria**: un comando registrato come inviato non dimostra che il gioco lo abbia ricevuto.

Il percorso previsto è **Lobby → Gioca → Caricamento → Routine → Risultati → Gioca di nuovo**. I clic dipendono dal riconoscimento dei pulsanti e dai controlli sulla finestra; una schermata non riconosciuta può interrompere il percorso.

<p align="center"><img src="assets/rarity-divider.svg" width="100%" alt="" /></p>

## Prima di salire sul Battle Bus

- **Progetto sperimentale:** il ciclo completo non è certificato su Fortnite reale.
- **Modello visivo:** classificatore per esempi di layout, non rete neurale. Il modello incluso usa 72 esempi di training; sulle 24 immagini separate di verifica ne riconosce 21 e si astiene su 3. Questi risultati non dimostrano affidabilità in partite indipendenti.
- **Nessuna IA di combattimento:** le routine descritte non mirano, costruiscono o combattono autonomamente.
- **Interfaccia variabile:** risoluzione, scala HUD e aggiornamenti del gioco possono richiedere una nuova calibrazione.

Dettagli del modello: [manifest](assets/pretrained/manifest.json) e [report visivo](assets/pretrained/visual.json). Per i blocchi del ciclo consulta la [guida diagnostica](docs/IGNORANTE_DIAGNOSTICA.md).

## Officina

Esegui le verifiche dal checkout con le dipendenze di sviluppo installate:

```powershell
python -m pytest -q
python studio.py --smoke-test
```

Per ricreare il modello, servono gli screenshot originali locali:

```powershell
python tools/train_visual.py --manifest assets/pretrained/manifest.json --images frames
```

Per compilare su Windows:

```bat
build_exe_windows.bat
```

Le istruzioni per build e installazione sono in [CREA_INSTALLER_WINDOWS.md](CREA_INSTALLER_WINDOWS.md). Prima di aggiornare un'installazione, conserva una copia dei dati seguendo la [guida ai backup](docs/REPOSITORY_DATI_LOCALI.md).

## Inventario delle guide

- [Guida italiana e aggiornamenti](LEGGIMI.md)
- [Diagnostica Ignorante](docs/IGNORANTE_DIAGNOSTICA.md)
- [Dati locali e backup](docs/REPOSITORY_DATI_LOCALI.md)
- [Build Windows e installer](CREA_INSTALLER_WINDOWS.md)

<p align="center"><img src="assets/rarity-divider.svg" width="100%" alt="" /></p>

<p align="center"><strong>OYE · Osserva. Registra. Rivedi.</strong><br />Licenza <a href="LICENSE">GPL-3.0</a> · Progetto indipendente, non affiliato a Epic Games.</p>
