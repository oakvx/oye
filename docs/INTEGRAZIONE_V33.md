# Integrazione v33: flussi, autorizzazioni e persistenza

## Ownership

- `LearningStore` possiede le righe `frames`, `agent_decisions` e gli indici delle dimostrazioni. `FastActionPolicy` consuma solo esempi umani e convalidati, non tutte le immagini archiviate.
- `Runner` mantiene l'ultimo frame fresco, gli stati del focus/pausa, il risultato OCR e la decisione dell'utente di abilitare LIVE. I suggerimenti ML non sono comandi diretti.
- `SingleMatchController` è proprietario di `await_play`, `joining`, `jumping`, `exploring`, `ending`, `post_lobby`, `claim_rewards`, `done`. Convalida lo stato prima di invocare l'esploratore.
- `GuidedExplorer` decide se accettare un suggerimento di movimento, alterna lo stile di navigazione e gestisce i recuperi dall'immobilità. Blocca azioni non di locomozione proposte dal ML.
- `RewardOCRWorker` rileva il titolo separatamente dai pulsanti; `reward_title` insegna un **testo non cliccabile**. Servono OCR recenti e ripetuti per Riscatta.
- `CycleStore` contiene soglie e transizioni per il ciclo ricompense, senza riscrivere i dati di addestramento. Alcuni limiti di sicurezza restano implementati in codice: SQLite non può disabilitarli.

## Priorità di una decisione

1. Stop/F9, Pausa/F8, focus perduto, frame obsoleto, OCR di fase indisponibile: nessun input.
2. Spettatore / attesa riavvio / caricamento: nessun input.
3. Primo indizio recente di titolo ricompense: sospensione della locomozione; solo dopo seconda acquisizione distinta transizione al riscatto.
4. Menu ricompense: solo Riscatta, verificato e con retry limitati; mai Equipaggia.
5. Ritorno alla lobby e verifica ricompense prima della ripartenza.
6. Partita: salti previsti dal controller, recupero ostacoli, azioni di navigazione apprese (se valide), routine locale di fallback.

## Compatibilità e backup

- Lo ZIP patch non trasporta `config.json`, `learning/`, `templates/`, `state/`, screenshot o registrazioni.
- Il preparatore crea `'_OYE_BACKUP/Oye_dati_prima_v33_*.zip'` al primo avvio di v33, usando l'API di backup SQLite per includere le transazioni WAL commit.
- Le tabelle `oye_cycle_*` vengono aggiunte con `CREATE TABLE IF NOT EXISTS`, evitando riscritture degli apprendimenti precedenti.
- Per dati corrotti l'app conserva il file originale e segnala il problema anziché eliminare il database.

## Cosa non è dimostrato

La suite è offline e non sostituisce il collaudo su Windows installato né una prova nel gioco. Una policy kNN basata su screenshot può suggerire movimenti riconosciuti ma non fornisce pianificazione spaziale completa.
