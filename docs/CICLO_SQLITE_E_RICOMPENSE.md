# Oye v32 — comportamento preciso del ciclo e regole SQLite

Questa versione **aggiunge regole e storico nel database di apprendimento già esistente** `learning/observer.sqlite3`. Non sostituisce, resetta o converte in blocco `frames`, `agent_decisions`, screenshot, etichette o registrazioni. Le tabelle `oye_cycle_*` vengono create solo se assenti e le regole predefinite vengono inserite con `INSERT OR IGNORE`. Una modifica valida ai tempi/conferme già presenti in SQLite viene mantenuta all'aggiornamento successivo. Le transizioni consentite e i nomi delle azioni sono vincolati nel codice per evitare di eseguire comandi arbitrari da un database manomesso.

## Sequenza operativa

1. **`await_play` (lobby prima della partita):** cerca `GIOCA` nell'area mostrata in «Fammelo vedere»; l'azione viene permessa soltanto dopo le conferme di Brain e se non è visibile un pannello ricompense. Le ricompense hanno sempre priorità maggiore di Gioca.
2. **`joining` (attesa):** dopo un clic `GIOCA` realmente inviato, attende caricamento/ingresso. In `Avvio rapido`, dopo 20 s e con schermate non bloccanti, può avviare la routine temporizzata. Il controllo delle schermate spettatore e attesa riavvio rimane attivo.
3. **`jumping` / `exploring`:** salto, WASD, rotazione camera e click. Se compare una schermata di fine partita confermata si passa a `ending` e si sospendono gli input di gioco.
4. **`ending` (risultati):** cerca `CONTINUA` o `TORNA ALLA LOBBY`, mai `GIOCA`. Aspetta tre *fotogrammi nuovi* della lobby con Gioca prima di contare una partita completata. Se la partita torna direttamente alla lobby senza risultati, ne richiede cinque.
5. **`post_lobby` (lobby dopo la partita):** questa fase è **obbligatoria**, anche se la lobby sembra già pronta. Se il pannello ricompense copre immediatamente la lobby dopo risultati già verificati, il titolo ricompense confermato consente di entrare in `post_lobby` senza richiedere un pulsante Gioca invisibile; non autorizza alcun click. Per almeno **18 secondi** controlla ricompense; non può cliccare Gioca. Lo scanner OCR valuta lo screenshot circa ogni **2 secondi**. Se la schermata mostra `RICOMPENSA OTTENUTA`, `RICOMPENSE OTTENUTE`, un titolo troncato compatibile, o l'equivalente inglese, servono **due letture OCR distinte e recenti** prima di passare a `claim_rewards`. Per saltare la fase in assenza di ricompense servono **18 secondi**, **tre nuove conferme lobby** e **almeno due scansioni OCR valide ottenute durante la fase**. Se l'OCR non risponde, Oye aspetta e mostra un errore invece di ripremere Gioca.
6. **`claim_rewards` (riscatto):** cerca solo il pulsante `RISCATTA` (o `CLAIM`), preferendo il ritaglio insegnato dall'utente. Il titolo ricompensa deve essere ancora confermato; un generico `RISCATTA` fuori contesto non basta. Il puntamento OCR va confermato con **almeno due rilevazioni stabili** nella stessa posizione (tolleranza 48 px) e l'input con le conferme del motore menu. **`EQUIPAGGIA` è vietato in questo ciclo.** Dopo il clic attende la scomparsa del pannello e la lobby verificata su **tre nuovi fotogrammi**, trascorso almeno **1 secondo**. Se il clic non ha effetto, può ritentare **una sola volta dopo almeno 12 secondi**; se resta bloccato, chiede di mostrargli di nuovo il pulsante. Se un utente chiude manualmente il pannello, dopo 18 s e tre conferme lobby prosegue senza inventarsi un click.
7. **Ritorno a `await_play`:** aspetta altri **6 secondi**, azzera il timer di salti/movimenti, poi è autorizzato a premere `GIOCA` solo dopo verifica visiva. `Ciclo continuo` ripete la sequenza; `Una sola partita` termina dopo la fase ricompense.

**Stati non riconosciuti:** nessun clic alla cieca su `RISCATTA` o `GIOCA`, nessun salto mentre è riconosciuta una schermata di caricamento/spettatore/ricompense. Stop/F9 arresta tutto. Se l'OCR non funziona o il pulsante non è identificabile, Oye resta in attesa e la dashboard indica cosa mostrare; non inventa coordinate.

## Tabelle aggiunte

| Tabella | Contenuto |
|---|---|
| `oye_cycle_rules` | `stage`, `event`, `next_stage`, `action`, `confirmations`, `wait_seconds`, `priority`; regole del flusso realmente lette dal controller |
| `oye_cycle_state` | ultima fase e numero partite, data e stato attivo; utile per diagnostica, non autorizza input automatico dopo riavvio |
| `oye_cycle_history` | transizioni, avvio partite e click Riscatta realmente inviati; non registra ogni OCR né gli screenshot |

Puoi esaminare le regole sul tuo PC con qualsiasi visualizzatore SQLite (a Oye fermo). I tempi e il numero di conferme validi sono applicati al successivo avvio. Le transizioni e i nomi delle azioni rimangono vincolati dal codice per sicurezza.

## Conservazione dei dati quando aggiorni la cartella

- **Non cancellare la cartella vecchia.** Estrai lo ZIP di aggiornamento **nella cartella radice in cui c'è già `AVVIA_OYE.bat`**, sovrascrivendo i file di programma.
- Lo ZIP **non contiene** `config.json`, `templates/` dell'utente, `learning/`, `state/`, `training/`, `screenshots/` o i log; quindi l'estrazione non può sostituire quei file con versioni vuote.
- Il primo avvio esegue `tools/preflight.py`: produce un archivio di sicurezza in `_OYE_BACKUP/`, fotografando SQLite tramite l'API di backup (incluso il journal WAL committato). Controlla i vecchi screenshot e poi avvia l'app.
- Anche se lo stesso update viene eseguito più volte, **le regole esistenti non vengono sovrascritte** e lo storico non viene azzerato.

## Cosa è stato collaudato

La macchina a stati, le regole SQLite, i vincoli sul riscatto e la conservazione dei dati sono verificabili con test automatici/offline. Il click sintetico dentro Fortnite, il pacchetto EXE e la combinazione esatta di schermate del tuo account richiedono una prova su Windows: non vengono dichiarati verificati senza tale prova.
