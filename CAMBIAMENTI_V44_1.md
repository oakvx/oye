# Hotfix v44.1 — Lobby GIOCA

- `observer/single_match.py`: la fase `joining` può generare `menu_click` dopo tre conferme fresche del pulsante GIOCA con punto valido, anche quando Brain propone `wait`; al massimo tre clic, cooldown 12 s; reset a ogni ciclo.
- `observer/runner.py`: passa al controller il punto reale del match visuale `gioca`; resta obbligatoria la verifica di focus e di OCR/visione recente.
- `oye_web_app.py`: segnala esplicitamente il focus perso e la necessità di rientrare in Fortnite; pulisce il messaggio quando il focus torna.
- `tests/test_v45_lobby_click.py`: nuove prove per WAIT di Brain, mancanza coordinate, assenza sconosciuta, cooldown, retry e rifiuto del driver.
- Aggiornamento sorgente additivo senza database, training, template o config personali.

Non verificato: click ricevuto in Fortnite su Windows.
