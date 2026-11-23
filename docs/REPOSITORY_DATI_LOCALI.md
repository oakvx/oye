# Dati locali e repository

Il repository è privato al momento della verifica. Il commit iniziale contiene già file di runtime tracciati: database SQLite e WAL/SHM, log di sessione, stato, bytecode e metadati con percorsi Windows locali. Un `.gitignore` non rimuove file già tracciati né li cancella dalla cronologia. Non è stata effettuata una scansione esaustiva dei segreti, né una riscrittura della cronologia: il proprietario deve verificare l'accesso al repository e valutare eventuali dati personali o credenziali già presenti; se ci fossero credenziali valide, revocarle e ruotarle prima di qualunque risanamento della cronologia.

I nuovi file in `learning/`, `logs/`, `state/`, `templates/`, backup e cache sono ignorati. Anche `config.json` è ignorato per le copie nuove, ma la copia legacy già tracciata resta tracciata; la configurazione iniziale della build rimane `defaults/config.json`.

Non sono stati rimossi file o dati. Per rimuovere in futuro gli artefatti tracciati, prima chiudere Oye e fare un backup verificato di configurazione, database e relativi WAL/SHM, frame, template e stato; poi pianificare una migrazione esplicita e, se necessario, un intervento sulla cronologia coordinato con il proprietario. La rimozione dall'indice da sola non ripulisce i vecchi commit.

La CI Windows costruisce e verifica l'applicazione e conserva l'artefatto soltanto nelle GitHub Actions del repository; non pubblica release. Non include automaticamente registrazioni, database o log locali nell'artefatto. La disponibilità e durata degli artefatti dipendono dalle impostazioni del repository.
