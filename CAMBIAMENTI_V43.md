# Oye v43 — Contatori dei rilevamenti OCR

## Comportamento

- I lettori OCR a regioni della v42 continuano a usare un unico frame condiviso, secondo la frequenza del worker.
- Gli OCR già presenti per Spettatore, Attesa del riavvio, Ricompense, Riscatta, Continua e Ritorna alla lobby alimentano gli stessi contatori; non introduciamo nessuna seconda cattura.
- Una lettura OCR riuscita incrementa `session_scans`; un testo riconosciuto incrementa `session_hits`.
- La comparsa **confermata** di un testo incrementa `session_total` e `all_time_total` una sola volta, non a ogni frame.
- Due osservazioni distinte sono richieste per gli scanner OCR modulari; per l'OCR legacy si utilizza il gate di stabilità già presente.
- La scomparsa su due letture negative riabilita un futuro conteggio; errori, OCR non disponibile e UNKNOWN non equivalgono ad assenza.
- Il registro di Oye Mini mostra `OCR trovato: ...`, con testo e riferimento.
- Mini e Studio mostrano contatori, ultimi rilevamenti e risultati per elemento.

## Database e migrazione

Le tabelle `oye_ocr_detection_counts` e `oye_ocr_detection_events` sono create automaticamente in `learning/observer.sqlite3` e non alterano le tabelle esistenti. Sono archiviati massimo 300 eventi recenti, mantenendo i contatori complessivi. Se SQLite è corrotto, il conteggio di sessione funziona ancora in memoria, senza bloccare Oye.

## Test

I test aggiuntivi verificano deduplicazione, due conferme, errori OCR, persistenza, interazione con il worker e lettori esterni già presenti nel Runner. Non è stata effettuata una partita reale; lo scanner continua a dipendere dalla qualità delle ROI, dalle impostazioni e dalla disponibilità di Tesseract per i metodi testuali.

## File modificati

`observer/ocr_detection_counter.py` (nuovo), `observer/region_ocr.py`, `observer/runner.py`, `oye_web_app.py`, `web/scanners.html`, `web/scanners.js`, `web/scanners.css`, `web/mini.html`, `web/mini.js`, `web/mini.css`, `tests/test_v43_ocr_discoveries.py` (nuovo), `VERSION.txt`, script di avvio e installer (solo numero versione).
