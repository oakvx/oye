# Oye v37 — Web Studio

- Nuova identità visiva desktop: layout, transizioni animate, scansione live, colori e feedback, CSS responsive e modalità a movimento ridotto.
- Cinque sezioni nell'applicazione web: Panoramica, Insegna, Archivio, Timeline, Impostazioni.
- Archivio e timeline delle partite con sorgente dati unica `learning/observer.sqlite3`.
- Riproduzione dei fotogrammi, tracce eventi/input, correzioni, azioni apprese, note e annotazioni di intervalli.
- API locali con token, controlli sui percorsi delle immagini, esportazione JSONL e diagnostica input esplicita.
- Impostazioni di apprendimento, recupero ostacoli e Ollama accessibili via web.
- Tutti gli avvii Windows standard aprono la dashboard web, incluso il vecchio pulsante per l'editor avanzato.
- Regole di gioco, routine Ignorante e sistema OCR invariati rispetto alla v36.
- Aggiornamento sopra la versione esistente senza sovrascrittura di SQLite, screenshot, template o configurazione personale.

**Collaudo:** test Python e smoke test locali; verifica visuale statica in Chromium, verifica degli script JS con dati simulati. Da collaudare Windows/WebView2 e ricezione dei tasti nel gioco reale.
