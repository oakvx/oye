# Oye v38 — Avvio Ignorante e dimostrazioni facili

- In modalità Ignorante della dashboard, serve il riferimento **Gioca** (insegnabile dalla pagina Insegna a Oye); non serve riconoscere bus o HUD.
- Quando il pulsante Gioca è visibile Oye non invia movimento. Lo può cliccare se il riconoscimento lo permette.
- Quando Gioca non è più visibile per tre screenshot distinti, parte il conteggio di **120 secondi** di attesa caricamento. Se Gioca ritorna, il timer viene azzerato.
- Dopo l'attesa invia **solo Spazio** ogni 0,8 secondi per una durata casuale compresa tra **120 e 150 secondi**, selezionata una volta per partita.
- Terminata la sequenza salti inizia la routine principiante con WASD, cambio di visuale, selezione piccone e clic sinistro.
- Timer e salti non hanno bisogno dell'HUD, ma se screenshot mancano *prima* di aver visto sparire Gioca, non inventa l'inizio della partita. Dopo che la scomparsa è stata verificata, la routine procede anche in assenza di nuove immagini.
- Gli stati confermati spettatore/ricompense possono sospendere movimento. Stop/F9, Pausa/F8 e focus Fortnite rimangono vincolanti.
- Pagina **Insegna a Oye**: Registra le mie mosse → giochi tu → Termina → Conferma le mosse registrate. Il mouse sinistro viene registrato per il training assieme a WASD e Spazio. La conferma esplicita riguarda solo gli esempi demo dell'ultima sessione in scene match/unknown, senza trasformare schermate sicuramente menu in esempi di movimento.
- I dati legacy in learning/, templates/, config.json e state/ non sono inclusi nello ZIP di aggiornamento. Prima dell'estrazione creare un backup.
- La ricezione degli input sintetici da Fortnite richiede un test Windows reale.
