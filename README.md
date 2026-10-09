# TelekElőírás AI

**Állapot: fejlesztés és ellenőrzés alatt. Nem hiteles, automatikus telekvizsgálati szolgáltatás.**

## Cél
Magyarországi település és helyrajzi szám alapján a hatályos helyi építési szabályzat (NJT), a szabályozási terv, az övezeti besorolás és az előírások forrásolt összekapcsolása. A térképi telek–övezet kapcsolatot bizonyítani kell, nem szabad becsülni.

## Fájlok
- `app.py` – a Streamlit alkalmazás.
- `plan_labels.py` és `plan_ocr_worker.py` – tervlapfeliratok feldolgozása; a worker a tervlapolvasás része.
- `rule_inventory.py` – a jogszabályi rendelkezések nyilvántartása.
- `geopdf.py` – a PDF saját földrajzi koordinátáinak és elnevezett poligonrétegeinek feldolgozása.
- `reference_checks.py` – az öt mintatelek megismételhető, élő hivatalos forrásellenőrzése.
- `test_plan_labels.py`, `test_rule_inventory.py` és `test_parcel_zones.py`, `test_automatic_sources.py` – automatikus és Streamlit-integrációs ellenőrzések.
- `requirements.txt` és `packages.txt` – futtatáshoz szükséges csomagok.
- `.github/workflows/regression.yml` – automatikus tesztfuttatás.

## Állapot és ellenőrzés
Az öt vizsgálati helyrajzi szám: Tiszaújváros 2200/8; Budapest XII. 8448/46; Komádi 1558; Gersekarát 034/15; Miskolc 4755/11.

**Egyik telek teljes, hatályos, pontos övezeti besorolással és összes előírással alátámasztott automatikus vizsgálata sincs még igazoltan kész.** A tesztfájlok megléte önmagában nem jelent sikeres tesztfutást.

A régi verziószámokra és elavult fejlesztési állításokra épülő leírást eltávolítottuk. A régi Git-előzmények megmaradnak, mert a visszaállíthatósághoz szükségesek.

## Automatikus betöltés és felületi bekötés

A Streamlit vizsgálat és a parancssori ellenőrzés ugyanazokat a hivatalos
betöltőket és geometriai adaptereket használja. A HRSZ-szolgáltatás pontos
település/HRSZ-egyezéssel kérdezhető le; a külön geometrialekérés hibája nem
veszíti el a már igazolt HRSZ-találatot. Az OÉNY körvonala megjelenítési
poligonjelölt, önmagában nem igazolt telekhatár.

A `load_official_plan` az NJT-rendelet fejlécében ellenőrzi a települést és a
budapesti kerületet, kiolvassa az időállapotot, és kizárólag az ott hivatkozott
hivatalos tervet tölti le. A kiválasztott PDF eredeti SHA-256 lenyomata a
fájlalapú MuPDF-megnyitás után is megmarad. Az ismert tervkiadások változó
lenyomata új forrásellenőrzést igényel. Gersekarát forrásrekordja a tényleges
tervre mutat a szöveges HÉSZ-melléklet helyett; Budapest XII. rövid mellékletneve
ellenőrzött tervhivatkozással feloldható.

A `geopdf.py` a forrásban tárolt GEO-koordinátákat, a forrás saját földrajzi
alapfelületét és az EOV-vetületet használja. Zárt, külön földrészletréteg és a
poligonon belüli pontos HRSZ együtt igazol tervlapi telekgeometriát. Ez rajzi
pontosságú forrásbizonyíték, nem hiteles földmérési adat. Forgatott/vágott lap,
hiányzó GEO-adat vagy nem támogatott réteg nem kap kitalált koordinátákat.

Az `identify_parcel_zones` a forrásból származó zárt övezetpoligonok teljes fedését
vizsgálja. Több övezetnél külön arányokat tart meg; hiányos vagy ellentmondó
fedést nem igazol. A szöveges címkéket tartalmazó SZT-réteg fehér háttereit nem
tekinti övezetpoligonnak. A meglévő tiszaújvárosi raszteradapter és a XII.
kerületi MINERVA/PDF-adapter eredménye is ezen bizonyítékkapcsolaton megy át;
egy felismert felirat nem pótolja a hiányzó poligonokat.

A felület négy külön bizonyítottsági állapotot mutat: HRSZ, telekgeometria,
övezet, teljes telekspecifikus előíráskör. A forrásból kinyert jogszabályi
rendelkezések megléte nem igazolja az összes előírás telekre alkalmazhatóságát.
A bizonyítékok JSON-ban letölthetők; a letöltőgomb a gyorsítótárazott számításon
kívül fut.

## Megismételhető ellenőrzés

A `requirements.txt` Python-csomagjai és a `packages.txt` OCR-csomagja szükséges:

```sh
python -m unittest discover -v
python reference_checks.py --output work/reference-results.json
python reference_checks.py --outlined --output work/reference-outlined-results.json
streamlit run app.py
```

A teljes regressziós csomag 60 teszt, köztük a 36 eredeti ellenőrzés és a teljes
Streamlit vizsgálati útvonal tesztje. A GitHub Actions a főágon és a főágra
nyitott pull requesteken is futtatja. Az élő ellenőrzés külön parancs: nem függ
külső szerverek aktuális elérhetőségétől az offline regresszió.

Az `--outlined` opció a görbévé alakított HRSZ-feliratokat is feldolgozza;
nagy CAD-terveken részleges, folytatható eredménnyel is zárulhat. A hiányzó
felirat vagy API-találat nem bizonyítja a telek hiányát. Az aktuális öt mintatelek
forrásait és korlátait a `validation/` eredményfájlok rögzítik; a pillanatnyi
elérhetőség és forráskiadás később megváltozhat.
