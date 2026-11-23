# Analisi baseline Ignorante

## Tre test segnalati

- **Minimappa e inventario:** i due test fallivano davvero. Le versioni di OpenCV possono restituire i segmenti Hough come `(N, 4)` oppure `(N, 1, 4)`. Il codice accettava soltanto la seconda forma: con la prima produceva `TypeError` e lo scanner riportava `error`. Ora accetta entrambe senza modificare soglie o validazione degli scanner. Le immagini dei test sono sintetiche: non dimostrano riconoscimento affidabile in Fortnite.
- **Profilo Gioca:** il test assumeva Tesseract disponibile sulla macchina di prova. La readiness richiede intenzionalmente OCR installato e abilitato per una regola solo testo. Il test ora simula esplicitamente entrambe le condizioni, lasciando il comportamento invariato. Un profilo OCR non è considerato pronto quando l'OCR è assente.

In questa verifica, la suite completa ha riportato 285 test passati e 4 saltati (più 4 subtest passati), su Linux e con dipendenze installate in un ambiente Python temporaneo. Non equivale a un collaudo Windows/Fortnite.

## Input e protezioni già presenti

- `observer/driver.py`: con il backend predefinito `keyboard`, usa PyAutoGUI per tastiera e mouse. Non si deve presumere che Fortnite riceva un comando solo perché la chiamata è riuscita. Non è stato introdotto un nuovo backend o modificato l'input.
- `observer/gamepad.py`: esiste anche un backend opzionale `gamepad`, selezionabile tramite configurazione, che usa `vgamepad` e un controller virtuale. **Non è il backend predefinito** (`input_backend=keyboard`). Il funzionamento e le implicazioni del controller virtuale vanno valutati separatamente; non è stato attivato da questa verifica.
- `observer/windows.py`: verifica il processo della finestra in primo piano tramite il suo eseguibile, ricava l'area client e attiva la DPI awareness. La lista degli eseguibili consentiti è configurabile. La sola verifica del processo non dimostra la ricezione degli input.
- `observer/runner.py` e `observer/driver.py`: stop, pausa e focus sono controllati prima delle azioni e durante molte attese; il codice prevede rilascio dei tasti e del mouse dopo le azioni, su perdita di focus e in chiusura. Alcune operazioni di PyAutoGUI sono chiamate singole: non è una garanzia di controllo atomico del focus durante ogni chiamata OS.
- `oye_web_app.py`: F8/F9 sono gestiti da un listener `pynput` separato durante le sessioni live. Non è stato verificato su Windows che il listener rimanga operativo in tutte le condizioni.
- `observer/input_probe.py`: esistono già prove esplicite per W e per il movimento della camera, con confronto visivo facoltativo. **Inviano input**, non sono diagnostica passiva. Una variazione visiva è un indizio, non prova che Fortnite abbia accettato il comando. Questa analisi non le ha eseguite.
- `observer/menu_supervisor.py`: conferme, coordinate, cooldown e numero massimo di clic sono già presenti. Un clic inviato non è trattato come esito confermato.
- `observer/unknown_watchdog.py`: sorveglia la durata delle schermate sconosciute, non è un watchdog indipendente che arresta un thread bloccato. Ignorante conserva inoltre percorsi a timer che possono proseguire con scena sconosciuta dopo la partenza stimata: **UNKNOWN non è universalmente un blocco di movimento**.

## Prossimi passi consigliati

1. Acquisire con consenso un piccolo dataset di frame reali, anonimizzato e fuori Git, con risoluzione, lingua e fase etichettate; includere schermate ambigue e negative.
2. Riprodurre i frame offline, misurando falsi positivi e negativi, prima di cambiare soglie e transizioni.
3. Su Windows fare prima un test passivo di cattura/focus e, solo se autorizzati, prove di input separate e interrompibili. Verificare anche se il gioco e Oye girano con privilegi compatibili.
4. Se il comportamento desiderato è fermarsi sempre su UNKNOWN, cambiare esplicitamente il contratto della modalità: oggi i timer consentono di procedere in alcune condizioni senza HUD riconosciuto.
5. Controllare le regole correnti della piattaforma prima di usare automazione in gioco. Nessuna tecnica di elusione anti-cheat è prevista.
