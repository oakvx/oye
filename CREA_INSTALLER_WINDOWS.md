# Oye v40 — Build Windows e disinstallazione

**L'archivio sorgente non è un installer compilato.** Per produrre `Oye-v40-Setup-Windows-x64.exe`:

1. Su un PC Windows 10/11 x64 installa **Python 3.11+** e **Inno Setup 6** (servono soltanto sulla macchina che crea la build).
2. Estrai tutto l'archivio, poi esegui `build_exe_windows.bat`.
3. Se dipendenze e test sono corretti, l'installer viene creato in `release/Oye-v40-Setup-Windows-x64.exe`.
4. Distribuisci **solo l'installer**: chi lo apre non deve installare Python né eseguire `pip`.

In alternativa, carica i file in un repository GitHub e avvia il workflow **Build Oye installer Windows** dalle Actions. L'artefatto sarà disponibile nella pagina della run.

## Installazione

- Per utente, senza richiesta di amministratore, in `%LOCALAPPDATA%\Programs\Oye`.
- Collegamento nel menu Start e disinstallazione da Impostazioni → App → App installate.
- Configurazione, screenshot, calibrazioni e archivio in `%LOCALAPPDATA%\Oye`, separati dall'applicazione.
- Aggiornamenti mantengono i dati esistenti. La disinstallazione chiede se conservarli o rimuoverli; il default è conservarli.
- Tesseract OCR **non è incluso**: se già installato sul PC non va reinstallato. Oye usa l'installazione di sistema.
- WebView2, se indisponibile, causa il ripiego nel browser locale.

## Limiti e continuità

Nella modalità **Ciclo continuo**, 120 minuti e 7.500 azioni sono soltanto soglie di contabilizzazione: il registro passa a un nuovo blocco senza arrestare Runner né azzerare la partita. Non sono previsti automatismi che superino F9/Stop, un blocco di sicurezza o un errore vero. La modalità **Una sola partita** mantiene l'arresto a fine partita e il limite anti-blocco del programma.

## Stato delle verifiche

I test Python sono eseguibili anche fuori da Windows, ma compilazione e avvio del `.exe`, verifica di WebView2, test su Fortnite e prova di disinstallazione devono essere effettuati su Windows. Senza la build Windows non è corretto presentare il file ZIP come un'app già installabile con un clic.


**Verifica essenziale:** lo script esegue `OyeStudio.exe --smoke-test` dopo PyInstaller; il workflow Windows ripete il test dopo l’installazione e controlla la conservazione dei dati dopo la disinstallazione. I test Linux non certificano l’installer.
