# Oye v29 P0 — interventi verificabili

## P0.1 — Stato «Pronto» coerente con Vision

- La dashboard verifica ciascun PNG calibrato con lo stesso decoder OpenCV usato dal motore.
- Esclude immagini non decodificabili, inferiori a 14 pixel per lato o troppo uniformi.
- Valida anche `_rois.json` e `_resolution.json`, anziché controllare soltanto la presenza del file.
- Mostra il nome del template non valido e permette di insegnarlo nuovamente con «Fammelo vedere».
- Se i metadati sono danneggiati, blocca l'avvio e segnala il problema. Non elimina automaticamente dati utente.

## P0.2 — Build/installazione/disinstallazione

- `studio.py --smoke-test` avvia realmente dashboard e server locale, testa HTML/CSS/JS, API con token e inizializzazione dei dati, senza aprire Fortnite né inviare input.
- `build_exe_windows.bat` esegue il test sull'eseguibile creato da PyInstaller **prima** di comporre l'installer.
- La GitHub Action esegue lo stesso smoke test **sull'eseguibile installato** e verifica che la disinstallazione lo rimuova ma conservi i file utente.
- La disinstallazione silenziosa conserva sempre i dati senza richiedere conferme. La disinstallazione interattiva continua a proporre la cancellazione facoltativa.

**Limite di verifica:** in questo ambiente Linux non è possibile creare o eseguire l'installer Windows. La validazione della build resta **da effettuare su un runner Windows**. Non dichiarare l'installer pronto finché il job Windows non è verde.

## P0.3 — Resilienza del ciclo continuo

- Timeout ingresso (240 s): in continuo Oye segnala un recupero, non chiude il Runner. Se ritrova la lobby verificata, riabilita la possibilità di premere Gioca dopo un cooldown; se non la ritrova resta in osservazione.
- Timeout risultati (240 s): segnala il riferimento mancante e aspetta una CTA verificata o una lobby stabile senza cliccare coordinate ignote.
- Schermata ignota (180 s): in continuo segnala il blocco ogni 180 s e continua a osservare senza input. Quando torna una schermata riconosciuta, azzera il watchdog.
- Modalità «Una partita» mantiene l'arresto di sicurezza sui timeout; Stop/F9 e Pausa/F8 continuano a funzionare.
- I limiti contabili per durata e azioni restano con rollover in ciclo continuo, come nella v28.

### Test

Da cartella sorgenti:

```shell
python -m unittest discover -s tests -q
python studio.py --smoke-test
```

Su Windows, per compilare e verificare l'installer: `build_exe_windows.bat`. Il file risultante sarà `release/Oye-v29-P0-Setup-Windows-x64.exe` **solo se il build Windows ha successo**.
