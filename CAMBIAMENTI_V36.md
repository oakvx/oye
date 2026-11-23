# Oye v36 — Ignorante con comportamento da principiante

## Modifiche

- `observer/quick_routine.py`: dopo un avvio riproducibile, brevi motivi di movimento variabili (camminata, deviazione, rotazione, cambio strada, accovacciata, picconate e salti). Nessuna dipendenza da OCR o HUD per la routine temporizzata.
- Continuità garantita dalla policy: movimento almeno ogni tre decisioni, picconata entro otto decisioni (se abilitata), salto entro undici decisioni anche dopo la finestra iniziale di 120 secondi.
- `observer/driver.py`: correzione del pulsante mouse mantenuto per errore dopo un clic destro seguito da clic sinistro. Ogni azione termina rilasciando i pulsanti effettivamente premuti.
- Dashboard: testo della modalità Ignorante e telemetria aggiornati.
- Build/installer: riferimenti di versione portati a v36, compresa la GitHub Action.
- `tests/test_v36_novice_activity.py`: test di continuità, varietà, input realmente emessi al mock e rilascio del mouse.

## Installazione sopra v35

1. Chiudere Oye e fare un backup della cartella corrente.
2. Estrarre il contenuto dello ZIP **nella stessa cartella** di `AVVIA_OYE.bat`, sovrascrivendo solo i file del programma.
3. **Non** cancellare `learning/`, `templates/`, `config.json`, `state/`, `logs/` o gli screenshot: non sono distribuiti in questo ZIP.
4. Avviare `AVVIA_OYE.bat` e selezionare Ignorante. In Fortnite, premere Pronto prima di Avvia e lasciare la finestra di gioco in primo piano.

## Limiti del collaudo

La suite di test e lo smoke test del server locale non verificano che Fortnite accetti i movimenti sintetici del mouse e della tastiera. Non è incluso un installer EXE compilato, né metodi per eludere anti-cheat o controlli sugli account. Un movimento variabile non garantisce che altri giocatori non segnalino il comportamento. Preferire sessioni private per le prove.
