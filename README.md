# TelekElőírás AI

**Állapot: fejlesztés és ellenőrzés alatt. Nem hiteles, automatikus telekvizsgálati szolgáltatás.**

## Cél
Magyarországi település és helyrajzi szám alapján a hatályos helyi építési szabályzat (NJT), a szabályozási terv, az övezeti besorolás és az előírások forrásolt összekapcsolása. A térképi telek–övezet kapcsolatot bizonyítani kell, nem szabad becsülni.

## Fájlok
- `app.py` – a Streamlit alkalmazás.
- `plan_labels.py` és `plan_ocr_worker.py` – tervlapfeliratok feldolgozása; a worker a tervlapolvasás része.
- `rule_inventory.py` – a jogszabályi rendelkezések nyilvántartása.
- `test_plan_labels.py` és `test_rule_inventory.py` – automatikus ellenőrzések.
- `requirements.txt` és `packages.txt` – futtatáshoz szükséges csomagok.
- `.github/workflows/regression.yml` – automatikus tesztfuttatás.

## Állapot és ellenőrzés
Az öt vizsgálati helyrajzi szám: Tiszaújváros 2200/8; Budapest XII. 8448/46; Komádi 1558; Gersekarát 034/15; Miskolc 4755/11.

**Egyik telek teljes, hatályos, pontos övezeti besorolással és összes előírással alátámasztott automatikus vizsgálata sincs még igazoltan kész.** A tesztfájlok megléte önmagában nem jelent sikeres tesztfutást.

A régi verziószámokra és elavult fejlesztési állításokra épülő leírást eltávolítottuk. A régi Git-előzmények megmaradnak, mert a visszaállíthatósághoz szükségesek.
