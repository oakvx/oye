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

## Diagnosi lobby intermittente

Il frame è acquisito dalla finestra verificata; `Vision` classifica direttamente i template, mentre il worker regionale OCR/scanner restituisce risultati asincroni. La fusione può confermare `LOBBY` solo su alcuni frame; prima gli altri frame ricadevano in `unknown`, azzerando il conteggio del supervisore. Ora soltanto una fusione LOBBY confermata insieme a una lettura positiva fresca dello scanner lobby conserva la scena per al massimo 7,5 s dal fotogramma originale. Non rinnova il tempo sui frame successivi. Scadenza, lettura negativa/fallita, perdita focus, cambio finestra o una scena incompatibile interrompono la conservazione. Il clic richiede comunque tre letture distinte e una lettura di bersaglio recente entro 4 s. Non equivale a una prova di due segnali indipendenti e la cadenza reale resta da misurare.

`ocr_available` nel log indica l'OCR delle **fasi**, non quello specifico del pulsante: diventa falso se la lettura più recente ha più di 12 s, se c'è errore o se non è ancora arrivata una lettura valida. Una perdita di focus può quindi causare il falso mentre il worker non riceve frame, senza dimostrare un guasto di inizializzazione. In Ignorante il supervisore sospende le conferme di menu fino a una lettura fasi valida e recente; dopo il recupero servono nuove conferme. Se l'OCR fasi è disabilitato o non installato, il menu automatico resta sospeso.

I sette screenshot forniti sono 1920×1080 e restano fuori dal repository. La lobby mostrata contiene il pulsante **PRONTO** e la scheda superiore **GIOCA**: non si deve assumere che quest'ultima avvii la partita. Non è stata effettuata una calibrazione o un'analisi statistica su questi soli frame; chiedere ulteriori esempi negativi e sequenze prima di modificare soglie o salvare template.
