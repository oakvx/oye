# Modalità Ignorante: verifica di Gioca

La sequenza della dashboard resta quella descritta in `IGNORANTE_V38.md`: osservazione di Gioca, eventuale clic autorizzato, conferma della scomparsa, attesa del caricamento, salti e routine. F8 mette in pausa, F9 ferma; Fortnite deve essere in primo piano.

L'OCR delle zone è asincrono e più lento dei fotogrammi. Una lettura positiva recente di Gioca può rimanere visibile al supervisore tra due letture, ma mantiene la data dello screenshot originale: **tre conferme richiedono tre letture distinte**. Coordinate scadute o una scena incompatibile non autorizzano il clic. Il numero di tentativi resta limitato e il comando inviato non prova che il gioco lo abbia ricevuto.

Se resta fermo, controlla il messaggio nella dashboard o l'evento `quick_wait` nel log:

- `confermo pulsante`: attendere nuove letture; se resta fermo calibrare la zona Gioca e controllare OCR e risoluzione.
- `attendo nuova acquisizione OCR`: la lettura precedente non viene contata due volte. Se persiste, controllare che lo scanner acquisisca nuove immagini.
- `coordinate non valide` o nessun pulsante valido: insegnare nuovamente il pulsante nella schermata corretta.
- `clic inviato`: verificare che Fortnite sia in primo piano e che la posizione insegnata sia corretta; l'esito resta da confermare nel gioco.
- `focus`: riportare Fortnite in primo piano. Non disabilitare il controllo del focus.

Questi controlli non sostituiscono una prova su Windows con Fortnite e non aggirano l'anti-cheat.

## Ciclo continuo desiderato

La dashboard avvia Ignorante in ciclo continuo: Gioca → caricamento → salti → movimenti semplici → fine partita → Gioca di nuovo. Quando entrambi i pulsanti dei risultati sono riconosciuti, Ignorante preferisce **Gioca di nuovo** a **Torna alla lobby**; questo può rimandare eventuali ricompense alla visita successiva in lobby. Se Gioca di nuovo non è disponibile, resta il percorso tramite lobby. Un pannello ricompense confermato e le protezioni spettatore/focus hanno priorità e sospendono il clic. Il riavvio è considerato confermato solo dopo il riconoscimento della schermata successiva. I pulsanti devono essere insegnati e visibili: senza riconoscimento non si clicca a caso.

## Acquisizioni OCR

Durante l'ingresso, le letture della zona Gioca hanno priorità; nei risultati ha priorità Gioca di nuovo. Le altre zone continuano a essere lette a rotazione: resta un solo OCR per acquisizione, per non sovraccaricare il sistema. Fotogrammi ripetuti o fuori ordine non vengono reinviati come nuove prove. Se cambia la risoluzione rispetto al profilo, le coordinate OCR conservate vengono scartate: selezionare o insegnare un profilo adatto alla risoluzione effettiva.

La scomparsa di Gioca richiede tre osservazioni negative distinte: una lettura OCR negativa conservata temporaneamente non viene contata più volte sui fotogrammi successivi. Finché mancano conferme, la routine resta in attesa.

## Clic e finestra reale

Prima di ogni clic di menu LIVE, Oye ricontrolla che la finestra in primo piano corrisponda a quella del fotogramma, che immagine e lettura siano recenti e che il punto sia nell'area client. Quando la finestra cambia posizione o perde il focus, le conferme e i risultati OCR in corso legati alla vecchia finestra vengono scartati; i tentativi di clic già inviati restano conteggiati. Un messaggio `menu_blocked` spiega il motivo del rifiuto. Questo riduce il rischio di coordinate obsolete, ma non elimina la possibile variazione di focus durante la singola chiamata al sistema operativo e non dimostra che il gioco abbia ricevuto il clic.

## Misura cadenza OCR (passo 1)

Flusso: cattura della finestra → `Vision` e OCR/scanner di ROI asincroni → fusione degli scanner e priorità della scena → `SingleMatchController` e `MenuSupervisor` → controllo finestra/freschezza → driver → nuova acquisizione per verificare l'esito. Il controller mantiene ancora propri stati e timer: non è ancora un'unica macchina a evidenza.

Con submit ogni 1,4 s, turni legacy/modulari alterni e target preferito ogni due turni legacy, l'intervallo teorico fra letture della stessa zona è **5,6 s**, oltre il precedente TTL di 3,4 s. La durata effettiva dipende dalla coda, dal tempo OCR e dai frame ricevuti; 5,6 s non è una misura su Fortnite. Quando la cache scadeva, `MenuSupervisor` poteva azzerare il conteggio. Ora la lettura positiva si conserva per massimo 7,5 s come **continuità diagnostica**, ma oltre 4 s non è utilizzabile per autorizzare un clic. Sono sempre necessarie tre letture con timestamp distinti; un'assenza o un errore aggiornato invalida la relativa cache. Un intervallo più lungo può ancora azzerare le conferme: non c'è garanzia di cadenza.

Il log `button_evidence` riporta per le nuove letture positive del template e le letture effettive OCR/scanner: pulsante, fonte, timestamp monotono originale, punteggio disponibile, stato, intervallo dalla precedente lettura positiva della stessa fonte ed età al momento dell'osservazione. Il timestamp monotono ha senso solo nella stessa sessione; il punteggio non è probabilità. I punteggi negativi dei template non sono attualmente esposti da `Vision`. Il log non dimostra che Fortnite abbia ricevuto input.

Per eventuali prove reali, verificare prima le regole di Epic e usare solo un account secondario che si è disposti a perdere: l'automazione può comportare sanzioni. Non sono previste tecniche di elusione dell'anti-cheat.

## Protezione menu e mouse

La lobby deve essere riconosciuta come tale prima di proporre il clic Gioca; una scena `unknown` non autorizza clic sui menu. Nella routine Ignorante i tasti del mouse (piccone, fuoco o clic destro) sono sospesi se l'HUD di partita non è stabile e autorizzato, anche quando manca la cattura. È una protezione aggiuntiva: i vecchi timer possono ancora inviare alcuni tasti di tastiera con scena incerta e non dimostrano l'atterraggio. Non è ancora implementata la nuova macchina a fasi richiesta.

Quando la scena è riconosciuta come lobby o caricamento, la routine Ignorante sospende anche i tasti di movimento; non tratta lo scadere dell'attesa come autorizzazione a saltare mentre il caricamento è ancora visibile. Questa protezione non sostituisce il riconoscimento visivo di bus e atterraggio, che resta da implementare.

## Regressione OCR asincrono: verifica sintetica

`tests/test_play_async_regression.py` simula frame ogni 0,15 s con tre consegne OCR nominalmente ogni 1,4 s o 5,6 s, scena lobby solo alla consegna e segnali sintetici template/OCR concordi. Prima della correzione le due varianti davano zero clic proposti; ora passano senza xfail. Esercitano l'accumulatore, il supervisore e il controller, ma non il worker OCR, l'intero runner, il driver o Fortnite. Ulteriori test coprono veto, cambio focus e letture scadute; non sostituiscono un replay su sequenze reali.

Il profilo scanner v42 contiene un riferimento template per una diversa disposizione della lobby; la configurazione predefinita dello scanner può invece utilizzare OCR. Il repository contiene il riferimento scanner, ma la cartella locale `templates/` di questo ambiente non contiene un template Gioca insegnato. Non è stato verificato che due rilevatori indipendenti riconoscano lo stesso pulsante sulla configurazione del PC di gioco. In particolare scanner modulare e OCR di zona possono usare entrambi OCR, mentre due template potrebbero derivare dallo stesso riferimento: nomi diversi non bastano a dimostrare indipendenza. Il nuovo screenshot di lobby mostra **PRONTO**; la scheda superiore GIOCA non va considerata un pulsante d'avvio. Prima di consentire un clic occorrono ROI del pulsante effettivo e due rilevatori diversi, calibrati e verificati sui frame locali positivi, negativi e ambigui. Nessuna soglia o template è stato cambiato sulla base del solo screenshot.

`observer/play_evidence.py` è collegato al runner: richiede tre coppie distinte template Vision e OCR della zona Gioca, punto nel rettangolo OCR, fonti entro 4 s e geometria costante. Le letture sono conservate al massimo 7,5 s fra coppie; perdite di focus, pausa e cambi finestra azzerano le conferme. Scena incompatibile, OCR fasi assente/scaduto in Ignorante, ricompense e spettatore sospendono la conferma. Non viene promosso UNKNOWN a lobby. Il controllo finale di focus, geometria e coordinate rimane invariato. Lo scanner OCR modulare e altri template non contano come secondo segnale. La nuova integrazione non è stata verificata su Fortnite né su un replay completo; un rilevamento erroneo non può essere escluso.

## Scheletro locale dai sette screenshot

`python tools/personal_skeleton.py` legge `personal-dataset/manifest.json` e scrive `personal-dataset/skeleton-report.json` (entrambi ignorati da Git). `--manifest`, `--templates` e `--output` permettono percorsi locali diversi; assicurarsi che anche tali percorsi siano esclusi da Git. I frame sono trattati come immagini **indipendenti**, non come una sequenza temporale: il report confronta la classificazione Vision con i singoli scanner template v42 predefiniti, non con l'intera pipeline, i profili utente o i clic. Le etichette sono preliminari, non ground truth; gli output non sono una matrice di confusione validata. In particolare il punteggio `absent` dello scanner template è un punteggio di assenza, non la correlazione originale. Niente soglie o template vengono modificati e nessuno screenshot viene copiato nel repository.

Nel checkout di questa sessione Vision non ha template locali e restituisce UNKNOWN sui sette frame. Lo scanner v42 lobby segnala `absent` anche nel frame con PRONTO visibile: non è una verifica affidabile del pulsante corrente. Gli scanner predefiniti riconoscono alcuni elementi nei frame etichettati risultati e spettatore; ciò non prova generalizzazione né una sequenza giocabile. Servono ulteriori frame positivi, negativi e ambigui e un replay completo prima dell'uso operativo.

### Database di calibrazione preliminare condiviso

Su richiesta è disponibile `assets/calibration/profilo_preliminare_1920x1080.sqlite3`: contiene solo un profilo 1920×1080 e cinque regole ROI/testo (PRONTO, ritorno lobby, gioca di nuovo, caricamento e discesa). Non contiene immagini, ritagli o storico partite. È un **esempio isolato**, non viene caricato automaticamente e non va copiato sopra `learning/observer.sqlite3`, che contiene dati personali. Mancano i template personali e la verifica OCR indipendente: il database da solo non rende operativo il ciclo. I riferimenti immagine personali restano fuori da Git. Per un'anteprima usa `python tools/import_calibration.py --root PERCORSO_OYE`; aggiungi `--apply` per inserire solo le regole mancanti nel profilo selezionato 1920×1080 borderless. Le regole esistenti non vengono sovrascritte. Esegui un backup del database prima dell'importazione e riavvia Oye; non vengono importati template personali. Il comando non importa template personali né garantisce clic; la conferma richiede anche un template locale valido e OCR funzionante.
