# Oye v30 — Correzioni P1

Questa versione parte **dai sorgenti v29 P0**. I P2 non sono stati modificati.

## Modifiche incluse

1. **Telecamera**: il pianificatore esegue brevi prove di rotazione senza W a intervalli; un verificatore misura i fotogrammi successivi. Due prove isolate senza risposta visibile producono un avviso diagnostico. La rotazione insieme al movimento non viene più scambiata per conferma della telecamera. L'invio `pyautogui.moveRel` rimane invariato: non è dimostrato che Fortnite accetti questo input.
2. **Movimento e ostacoli**: l'esploratore usa un indizio conservativo di densità dei bordi nelle porzioni laterali della scena per scegliere una direzione nei tentativi di recupero. Alterna retromarcia, rotazione, spostamento laterale, salto e nuova misura di movimento. Dopo ripetuti fallimenti interrompe temporaneamente i movimenti e ritenta con una pausa crescente fino a 90 s. Questo NON è riconoscimento geometrico degli ostacoli né mappatura 3D: le scene animate possono confonderlo.
3. **Ripresa dopo chiusura imprevista**: il ciclo automatico salva un checkpoint atomico in `state/auto_cycle.json` (nella cartella dati di Oye). Alla successiva apertura, dopo la conferma manuale della modalità LIVE, recupera il contatore e verifica almeno tre nuovi fotogrammi distinti. Riprende dalle fasi lobby, partita, risultati o bus solo quando gli indizi sono affidabili. Non riapplica automaticamente comandi salvati e non si avvia da solo dopo Stop/F9.
4. **Calibrazione più flessibile**: puoi attivare **Osserva soltanto**, entrare manualmente in una partita e usare «Fammelo vedere» su schermate reali senza interrompere l'osservazione. Si usa una copia del fotogramma a **risoluzione originale** già acquisito da Oye; non una miniatura compressa né una seconda cattura conflittuale. Durante le modalità LIVE la modifica dei riferimenti rimane bloccata.
5. **Diagnostica evidente**: le anomalie OCR, gli errori del motore, l'impossibilità di scrivere checkpoint e i problemi della telecamera appaiono nella pagina principale con un suggerimento operativo; non solo nel registro eventi. La dashboard espone inoltre lo stato della verifica telecamera, del recupero e del checkpoint.

## Limiti di validazione

I test in questo pacchetto sono eseguibili su Linux e Windows, ma **non provano** l'accettazione dei tasti e del mouse da parte di Fortnite, né la compatibilità con l'anti-cheat. Non sono stati eseguiti una partita reale o un installer compilato Windows. La verifica Windows resta necessaria prima di una release destinata agli utenti finali.

## Test consigliati

```
py -3 -m unittest discover -s tests -v
py -3 studio.py --smoke-test
```

Per la build dell'installer su Windows usa `build_exe_windows.bat` e controlla anche avvio, Stop/F9, calibrazione durante «Osserva soltanto», salvataggio, disinstallazione e riapertura dopo chiusura anomala.
