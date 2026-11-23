# Oye v39 — Mini e riconoscimento a zone

## Avvio

Apri `AVVIA_OYE.bat`: la finestra predefinita è **Oye Mini**, con stato reale, Avvia/Pausa/Stop, partite e log. Il pulsante **Apri Studio** porta all’editor web. Le vecchie interfacce Tk/Qt non sono il percorso standard.

## Prima configurazione per Fortnite

Apri **Studio → Schermate e OCR**. Usa **Rileva da Fortnite** (hai cinque secondi per portare il gioco in primo piano) oppure scegli manualmente **1920 × 1080, finestra senza bordi**. La risoluzione rilevata è la dimensione dei pixel catturati della finestra, non un valore indovinato.

Seleziona un elemento, per esempio **Gioca**, **Ritorna alla lobby**, **Ricompensa ottenuta** o **Riscatta**. Carica uno screenshot oppure acquisiscilo da Fortnite, disegna un riquadro stretto intorno al testo, indica la frase prevista e premi **Salva questa zona**. Puoi usare **Prova questo OCR** per leggere soltanto quel riquadro.

Ogni testo insegnato ha il proprio OCR localizzato, il proprio punteggio e due conferme da immagini distinte. Il riconoscimento usa un thread separato e scansiona al massimo quattro zone per ciclo, dando priorità ai testi rilevanti per la fase. Errori e risultati vecchi non vengono interpretati come testo assente. Il motore usa i vecchi sensori come fallback.

La minimappa, la salute e l'inventario sono **indicatori visivi**: vengono riconosciuti con i template già presenti, non forzati attraverso un OCR testuale senza scritte. Lo Studio permette di aggiungere anche una frase OCR a un riferimento qualora vi sia davvero un testo leggibile.

## Dove sono salvati i dati

- `learning/observer.sqlite3`: nuove tabelle additive `oye_resolution_profiles` e `oye_screen_rules`. Le vecchie dimostrazioni restano intatte.
- `learning/screen_profiles/<risoluzione>_<modalità>/`: template e testo OCR specifici della risoluzione. **I vecchi file in `templates/` non vengono sovrascritti.**
- `config.json`, `learning/frames/`, `state/`: immutati dallo ZIP di aggiornamento.

## Limiti

La frase libera «cosa significa» è una spiegazione salvata, non un programma eseguibile. Lo stato e l’azione successiva sono associati ai soli tipi che il controller già supporta. Un singolo OCR positivo non autorizza un clic. Nessuna garanzia che Fortnite accetti gli input sintetici: serve prova sul PC Windows. Nessun aggiramento anti-cheat.
