# Oye 44.2.0

Oye è un'app Python per Windows con una modalità sperimentale **Ignorante**: osserva schermate e può inviare input autorizzati per un ciclo di partita semplice. Non è verificato che Fortnite accetti i comandi o che il riconoscimento sia affidabile nel gioco reale.

L'automazione può violare le regole di Epic e comportare sanzioni: controlla le regole vigenti; eventuali test reali solo con un account secondario che sei disposto a perdere. Non sono previste tecniche di elusione dell'anti-cheat. F8 mette in pausa, F9 arresta, e Fortnite deve rimanere in primo piano.

- Diagnostica e limiti Ignorante: [docs/IGNORANTE_DIAGNOSTICA.md](docs/IGNORANTE_DIAGNOSTICA.md)
- Protezione e backup dati: [docs/REPOSITORY_DATI_LOCALI.md](docs/REPOSITORY_DATI_LOCALI.md)
- Versione: [VERSION.txt](VERSION.txt)

La CI verifica test e build, ma non esegue partite Fortnite. I risultati su dati simulati non sono una validazione nel gioco.

## Avvio su Windows

Chiudi Oye e fai un backup di `config.json`, `learning/`, `templates/`, `state/` e `logs/` prima di aggiornare o fare pull: Git può eliminare file prima tracciati. Avvia la versione sorgente con `AVVIA_OYE.bat`; il primo avvio può richiedere Internet per installare le dipendenze. Per le istruzioni di backup consulta [Protezione e backup dati](docs/REPOSITORY_DATI_LOCALI.md).

Per creare un installer su Windows 10/11 x64 servono Python 3.11+ e Inno Setup 6; esegui `build_exe_windows.bat`. La CI produce una build dell'app, non prova una partita Fortnite né la procedura di installazione/disinstallazione.
