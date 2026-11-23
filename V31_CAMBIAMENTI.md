# Oye v31 — Avvio rapido e insegnamento pulsanti

## Nuova modalità: Avvio rapido
- Nella dashboard è la prima modalità (la modalità tradizionale resta disponibile).
- Dopo la selezione autorizzata di **Gioca**, avvia una finestra di 20 secondi. Se non rileva lobby, caricamento, spettatore, schermata ricompense o un blocco OCR, avvia Space, WASD, azioni del piccone e rotazioni occasionali.
- Il bus e l'HUD **non sono prerequisiti** in Avvio rapido. È una routine temporizzata e **non dimostra** che il personaggio sia davvero controllabile.
- Il gioco deve essere in primo piano. Serve Tesseract OCR operativo per sorvegliare gli stati spettatore/riavvio; se mancano dati OCR recenti, i movimenti sono sospesi. F8 pausa / F9 stop.

## Pulsanti insegnati
- Puoi mostrare **Gioca, Torna alla lobby, Riscatta e Continua**, uno alla volta. L'assistente propone il successivo senza richiedere subito l'HUD.
- Conserva template PNG, regione normalizzata e frase OCR in `templates/_menu_text.json`.
- Il campo **Testo del pulsante** consente di correggere una lettura OCR imperfetta (esempio: «RITORNA ALLA LOBBY»).
- Il worker legge i pulsanti con Tesseract **circa ogni 2 secondi**, nelle regioni insegnate; accetta parti distintive di almeno 4 lettere (`LOBB`, `RITOR`, `CONTINU`), ma non frammenti generici di due/tre caratteri.
- Prima di cliccare richiede più rilevazioni consecutive nella stessa zona e una fase del ciclo compatibile. Non usa «Equipaggia» come sinonimo di Riscatta; non ripreme Gioca fino al ritorno confermato in lobby.
- Se un pulsante conclusivo non viene identificato, lo segnala nella dashboard chiedendo di mostrarlo (dopo aver arrestato gli input LIVE).

## Validazione
- Suite automatica con regressioni del progetto, test OCR parziale e test su una schermata sintetica elaborata con Tesseract reale.
- Smoke test server locale via `studio.py --smoke-test`.
- Nessuna compilazione dell'installer su Windows né test effettivo dentro Fortnite in questo ambiente.
