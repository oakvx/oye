# Oye v42 — Modifiche

- Importato profilo visivo a sei scanner derivati da immagini reali 1920x1080.
- Lobby e matchmaking distinguono il pulsante operativo dal tab Gioca permanente.
- Aggiunto rilevamento indipendente «In attesa del riavvio» per le fasi di spettatore senza intestazione.
- Introdotta scadenza dello stato dopo un lungo intervallo senza fotogrammi, anche quando la prossima osservazione è fresca.
- Aggiunta protezione su immagini uniformi per il template matching e vincolo esplicito di risoluzione per i riferimenti a pixel.
- Preservate calibrazioni e regole già personalizzate; migrazione SQLite additiva con marker di profilo.
- Aggiunto replay della raccolta originale con report, matrice e indicazioni degli stati sconosciuti.
- Integrati asset nella configurazione di packaging Windows (la build reale resta da verificare).

Vedere `docs/OYE_V42_SCREENSHOT_PROFILE.md` per limiti e verifiche mancanti.
