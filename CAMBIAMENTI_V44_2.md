# Oye v44.2 — Verifica e recupero unificati di tutti i menu

L'aggiornamento applica al ciclo della modalità **Ignorante / partita continua** un supervisore condiviso (`observer/menu_supervisor.py`). Non riguarda soltanto **Gioca**: controlla anche **Torna alla lobby**, **Continua**, **Riscatta** e **Gioca di nuovo** (quest'ultimo soltanto in ciclo continuo).

## Regole operative

1. La fase deve essere coerente con il pulsante (lobby, risultati o ricompense).
2. Il bersaglio e le sue coordinate devono provenire da un riconoscimento calibrato sul fotogramma corrente; non si inventano coordinate.
3. Il bersaglio deve essere confermato su **3 fotogrammi distinti**.
4. Il clic entra nel contatore tentativi soltanto se il driver lo ha inviato. La telemetria segnala esplicitamente `menu_sent`, non "clic riuscito".
5. Il risultato viene controllato su **2 nuove acquisizioni** con evidenza della transizione. Un'immagine scaduta, OCR fallito o schermata sconosciuta non equivalgono a successo.
6. Se lo stesso pulsante rimane visibile, nuovo tentativo soltanto dopo **12 secondi** e nuove conferme; **massimo 3 clic inviati** (2 per Riscatta). Poi attesa e richiesta di mostrare il pulsante corretto, non clic all'infinito.
7. Se compaiono contemporaneamente «Ritorna alla lobby» e «Gioca di nuovo», prevale **Ritorna alla lobby**, per non saltare l'eventuale schermata ricompense successiva. «Gioca di nuovo» è una ripartenza diretta opzionale quando non c'è il ritorno.
8. **Riscatta** è consentito solo con titolo ricompense confermato. **Equipaggia** non è una destinazione di clic automatico.
9. Spettatore, attesa riavvio, frame scaduti o focus perso sospendono l'input. **Stop/F9** e Pausa/F8 restano validi.

## Integrazione

- `observer/runner.py` usa il supervisore per proporre i clic al controller esistente, senza un secondo worker di acquisizione.
- `observer/single_match.py` distingue tra pulsante visto e clic autorizzato. Un passaggio confermato da «Gioca di nuovo» riarma il countdown senza falsificare la riuscita del clic.
- `observer/vision.py`, `observer/reward_ocr.py`, `observer/region_ocr.py`, `observer/menu_text.py` e `observer/teach_ocr.py` supportano il pulsante «Gioca di nuovo».
- Nella pagina **Insegna schermate** puoi mostrare separatamente quel pulsante. Non viene confuso con «Gioca» della barra superiore.
- `tests/test_v44_2_all_menu_flows.py` riproduce i percorsi di clic, retry, risultati, ricompense e ripartenza.

## Aggiornamento e limiti

Estrai lo ZIP sopra la tua installazione esistente, a Oye **chiuso**. Lo ZIP non contiene `learning/`, `templates/`, `state/` o `config.json`: i vecchi screenshot e gli esempi SQLite restano dove sono. Fai comunque una copia preventiva della cartella prima di sovrascrivere. Nessuna migrazione dati distruttiva.

Il collaudo è **offline** su controller e driver simulati; il test reale del focus, delle coordinate multi-monitor e dell'accettazione dei clic da Fortnite richiede Windows. Non include sistemi di elusione dell'anti-cheat.
