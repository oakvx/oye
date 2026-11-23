# Oye v44 — Training guidato e revisione degli esempi

## Obiettivo

Rendere realmente utilizzabili le dimostrazioni registrate dall'utente, evitando
che una schermata incerta diventi automaticamente un comando da apprendere.

## Flusso per l'utente

1. Apri **Oye Mini → Insegna le mosse** oppure **Studio → Insegna a Oye**.
2. Premi **Registra le mie mosse**. Quando Fortnite è in primo piano,
   Oye acquisisce soltanto i comandi già previsti dalla whitelist e gli screenshot.
   La registrazione è passiva: Oye non controlla il personaggio.
3. Premi **Termina registrazione**, quindi **Rivedi le mosse**.
4. Scegli una registrazione. Oye propone prima gli screenshot con stato incerto
   e distribuisce i campioni tra azioni differenti, fino a 12 per pagina.
5. Per ogni fotogramma puoi:
   - **Confermare** che eri effettivamente in partita (eventualmente correggendo l'azione);
   - **Scartare** il campione (scompare dalla policy, ma rimane nell'archivio);
   - **Saltare** la revisione senza approvarlo;
   - **Ripristinare** un campione precedentemente escluso, usando il filtro «Tutti».
6. Il riepilogo mostra *utilizzabili / da rivedere / esclusi / azioni diverse*.
   Non è un indice di precisione del modello.

## Integrazione esistente

- Stesso `learning/observer.sqlite3` e stesse immagini in `learning/frames/`.
- Migrazione **additiva** in `LearningStore`: colonne `training_excluded`
  (default 0) e `training_reviewed_at`. Non cancella né riscrive le vecchie dimostrazioni.
- `FastActionPolicy.refresh()` consulta gli stessi esempi tramite
  `LearningStore.action_training_samples()`: quelli esclusi non vengono restituiti.
  Una correzione umana dell'azione passa a `action_source='manual'`, così non viene
  sovrascritta dall'azione rilevata durante la pressione dei tasti.
- Un'etichetta umana di *lobby / caricamento / risultati / spettatore* esclude il
  campione anche quando la classificazione HUD precedente indicava *match*.
- Le schermate chiaramente non giocabili non possono essere approvate dal revisore.
  Per un fotogramma `unknown` occorre uno screenshot disponibile e conferma esplicita.
- L'endpoint di approvazione globale della vecchia dashboard restituisce un
  messaggio per passare al revisore, invece di approvare alla cieca.
- Nuove API autenticate: `GET /api/learning/review` e
  `POST /api/learning/review`, senza una seconda base dati.
- `training_summary()` conteggia i campioni esclusi come non utilizzabili.

## File principali aggiornati

- `observer/learning.py`: schema, filtri della policy, revisione, report e pagine.
- `observer/upgrade.py`: contatori coerenti in lettura su DB nuovi e precedenti.
- `oye_web_app.py`: API e asset statici del revisore.
- `web/training44.js`, `web/training44.css`: interfaccia guidata.
- `web/index.html`, `web/app.js`, `web/mini.html`, `web/mini.js`, `web/mini.css`.
- `tests/test_v44_training_review.py`, `tests/test_v44_training_http.py`.
- Script/versioni di avvio e configurazione build Windows.

## Limiti da verificare

- Test offline, server locale e UI simulata: **non** equivalgono a una prova
  su Fortnite o a una misurazione dell'accuratezza della policy su nuove partite.
- I campioni con stato HUD incerto richiedono conferma umana; nessun
  riaddestramento supervisionato automatico viene avviato dalla correzione.
- La policy ad azioni usa ancora un recupero di vicini visivi (few-shot):
  può rifiutare campioni troppo diversi e ripiegare sul comportamento esistente.
- L'installer `.exe` Windows non è incluso: questo è uno ZIP sorgente da
  sovrascrivere sopra la cartella esistente, con i propri dati preservati.
- Durante alcuni test precedenti sono comparsi `ResourceWarning` di connessioni
  SQLite da componenti legacy: non sono oggetto di questa modifica.
