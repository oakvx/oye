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
