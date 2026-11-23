# Dati locali e repository

Il repository è privato al momento della verifica. Il commit iniziale contiene già file di runtime tracciati: database SQLite e WAL/SHM, log di sessione, stato, bytecode e metadati con percorsi Windows locali. Un `.gitignore` non rimuove file già tracciati né li cancella dalla cronologia. Non è stata effettuata una scansione esaustiva dei segreti, né una riscrittura della cronologia: il proprietario deve verificare l'accesso al repository e valutare eventuali dati personali o credenziali già presenti; se ci fossero credenziali valide, revocarle e ruotarle prima di qualunque risanamento della cronologia.

I nuovi file in `learning/`, `logs/`, `state/`, `templates/`, backup e cache sono ignorati. Anche `config.json` è ignorato per le copie nuove, ma la copia legacy già tracciata resta tracciata; la configurazione iniziale della build rimane `defaults/config.json`.

Non sono stati rimossi file o dati. Per rimuovere in futuro gli artefatti tracciati, prima chiudere Oye e fare un backup verificato di configurazione, database e relativi WAL/SHM, frame, template e stato; poi pianificare una migrazione esplicita e, se necessario, un intervento sulla cronologia coordinato con il proprietario. La rimozione dall'indice da sola non ripulisce i vecchi commit.

La CI Windows costruisce e verifica l'applicazione e conserva l'artefatto soltanto nelle GitHub Actions del repository; non pubblica release. Non include automaticamente registrazioni, database o log locali nell'artefatto. La disponibilità e durata degli artefatti dipendono dalle impostazioni del repository.

## Pulizia dell'indice

Gli artefatti runtime sopra elencati sono stati rimossi **solo dall'indice Git** con `git rm --cached`; i file locali in questo ambiente non sono stati cancellati. **Attenzione:** quando si applica il commit tramite Git su un altro checkout, Git può cancellare dalla copia di lavoro i file precedentemente tracciati e ora rimossi dall’indice. Prima di fare pull, checkout o aggiornare qualsiasi installazione, chiudi Oye e fai una copia verificata di `config.json`, `learning/` (inclusi database, WAL/SHM e frame), `templates/`, `state/` e `logs/`. Per conservare un database SQLite coerente è importante chiudere Oye prima della copia. L'assenza di questi file in una nuova clonazione non migra automaticamente i dati di un'installazione precedente.

La cronologia non è stata riscritta: vecchi commit possono ancora contenerli. Non pubblicare il repository senza una valutazione e un'eventuale bonifica separata.

### Screenshot personali

`personal-dataset/` è esclusa da Git: può contenere screenshot e manifest locali, mai da inviare alla CI. La cartella può essere sostituita da un percorso esterno scelto dall'utente; se si sceglie un'altra cartella all'interno del repository, aggiungerla prima alle esclusioni Git locali (`.git/info/exclude`). Verificare con `git check-ignore` prima di aggiungere file. Le etichette del manifest locale sono preliminari; non costituiscono calibrazione. Non includere ritagli o immagini nel repository senza approvazione esplicita.
