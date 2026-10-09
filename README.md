# TelekElőírás AI

**Állapot: fejlesztés és ellenőrzés alatt. Nem hiteles, automatikus telekvizsgálati szolgáltatás.**

## Cél
Magyarországi település és helyrajzi szám alapján a hatályos helyi építési szabályzat (NJT), a szabályozási terv, az övezeti besorolás és az előírások forrásolt összekapcsolása. A térképi telek–övezet kapcsolatot bizonyítani kell, nem szabad becsülni.

## Fájlok
- `app.py` – a Streamlit alkalmazás.
- `plan_labels.py` és `plan_ocr_worker.py` – tervlapfeliratok feldolgozása; a worker a tervlapolvasás része.
- `rule_inventory.py` – a jogszabályi rendelkezések nyilvántartása.
- `test_plan_labels.py`, `test_rule_inventory.py` és `test_parcel_zones.py` – automatikus ellenőrzések.
- `requirements.txt` és `packages.txt` – futtatáshoz szükséges csomagok.
- `.github/workflows/regression.yml` – automatikus tesztfuttatás.

## Állapot és ellenőrzés
Az öt vizsgálati helyrajzi szám: Tiszaújváros 2200/8; Budapest XII. 8448/46; Komádi 1558; Gersekarát 034/15; Miskolc 4755/11.

**Egyik telek teljes, hatályos, pontos övezeti besorolással és összes előírással alátámasztott automatikus vizsgálata sincs még igazoltan kész.** A tesztfájlok megléte önmagában nem jelent sikeres tesztfutást.

A régi verziószámokra és elavult fejlesztési állításokra épülő leírást eltávolítottuk. A régi Git-előzmények megmaradnak, mert a visszaállíthatósághoz szükségesek.

## Automatikus telek–övezet azonosítás: első fejlesztési lépés

Az `app.identify_parcel_zones` ellenőrzött adatforrás-adapterekhez készült,
önállóan tesztelhető geometriai azonosítási lépés. Pontos településazonosító és
HRSZ, ellenőrzött telek- és övezethatár, azonos koordinátarendszer, valamint
azonos kiadású, ellenőrzött hatályos NJT-tervforrás és SHA-256 szükséges hozzá.
A forrásellenőrzési jelzőket az adapternek kell igazolnia; a függvény önmagában
nem hitelesíti a forrást. Teljes fedésnél egyetlen kódot vagy több övezetet és
területarányokat ad vissza. Hiányos fedés, különböző övezetek átfedése vagy puszta
határérintkezés esetén nem igazol besorolást.

Ez még nem kapcsolódik az alkalmazás automatikus forrásletöltési folyamatához.
A következő lépés a hiteles telekhatár és a hatályos terv övezetpoligonjainak
forrásadaptere, majd a felületi bekötés és a valós referenciatelkek ellenőrzése.
Közeli térképi feliratból továbbra sem lesz automatikusan igazolt övezet.

Ellenőrzés: `python -m unittest discover -v` a `requirements.txt` csomagjaival.
A javítás után a 36 eredeti teszt sikeres; az új geometriai és bizonyítékkezelési
ellenőrzésekkel a teljes csomag 44 tesztből áll. A GitHub Actions a főágon és a
főágra nyitott pull requesteken is futtatja a csomagot.
