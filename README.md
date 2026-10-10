# TelekElőírás AI

**Állapot: fejlesztés és ellenőrzés alatt. Nem hiteles, automatikus telekvizsgálati szolgáltatás.**

## Cél
Magyarországi település és helyrajzi szám alapján a hatályos helyi építési szabályzat (NJT), a szabályozási terv, az övezeti besorolás és az előírások forrásolt összekapcsolása. A térképi telek–övezet kapcsolatot bizonyítani kell, nem szabad becsülni.

## Fájlok
- `app.py` – a Streamlit alkalmazás.
- `plan_labels.py` és `plan_ocr_worker.py` – tervlapfeliratok feldolgozása; a worker a tervlapolvasás része.
- `rule_inventory.py` – a jogszabályi rendelkezések nyilvántartása.
- `geopdf.py` – a PDF saját földrajzi koordinátáinak, jelmagyarázat szerinti vonalainak és poligonjainak feldolgozása.
- `plan_legend.py` – saját hivatalos jelmagyarázat keresése, többoszlopos és kétsoros feliratok, natív/OCR-jelminták és tartós fájlgyorsítótár.
- `plan_connections.py` – natív határjelek és részleges tervi feliratkapcsolatok ellenőrzése.
- `plan_geometry_audit.py` – telekkel metsző, saját jelmagyarázati minták és tényleges kitöltött geometriák ellenőrzése, külön jelzett hiányokkal.
- `zone_parameters.py` – kódpozíciók feloldása a tényleges hivatalos paraméterjelmagyarázatból.
- `reference_checks.py` – az öt mintatelek megismételhető, élő hivatalos forrásellenőrzése.
- `test_plan_labels.py`, `test_rule_inventory.py` és `test_parcel_zones.py`, `test_automatic_sources.py`, `test_plan_legend.py`, `test_zone_evidence.py` – automatikus és Streamlit-integrációs ellenőrzések.
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
lenyomata új forrásellenőrzést igényel. Gersekarát forrásrekordja a 2019-es közigazgatási terület szabályozási tervére mutat;
a `2._mell_klet_H_SZ.pdf` félrevezető fájlnév ellenére tervlap, nem szöveges HÉSZ; Budapest XII. rövid mellékletneve
ellenőrzött tervhivatkozással feloldható.

A `geopdf.py` a forrásban tárolt GEO-koordinátákat, a forrás saját földrajzi
alapfelületét és az EOV-vetületet használja. Zárt, saját jelmagyarázat szerint azonosított telekhatár vagy az abban megnevezett
natív kataszteri CAD-réteg, a poligonon belüli pontos HRSZ és a megjelenítési
körvonallal való ellenőrzött egyezés együtt igazol tervlapi telekgeometriát. Ez rajzi
pontosságú forrásbizonyíték, nem hiteles földmérési adat. Forgatott/vágott lap,
hiányzó GEO-adat vagy nem támogatott réteg nem kap kitalált koordinátákat.

Az `identify_parcel_zones` a forrásból származó zárt övezetpoligonok teljes fedését
vizsgálja. Több övezetnél külön arányokat tart meg; hiányos vagy ellentmondó
fedést nem igazol. A címkehátterek nem övezetpoligonok. Egy felismert felirat nem pótolja a
hiányzó poligonokat. A korábbi tiszaújvárosi színalapú adapter és a XII. kerületi
MINERVA/PDF-adapter besorolási útvonala letiltva, amíg a saját jelmagyarázat
szerinti vonalosztályozásuk nem igazolt. A korábbi jelöltek nem bizonyított eredmények.

A felület négy külön bizonyítottsági állapotot mutat: HRSZ, telekgeometria,
övezet, teljes telekspecifikus előíráskör. A forrásból kinyert jogszabályi
rendelkezések megléte nem igazolja az összes előírás telekre alkalmazhatóságát.
A bizonyítékok JSON-ban letölthetők; a letöltőgomb a gyorsítótárazott számításon
kívül fut.

## Saját jelmagyarázat és újrafelhasználás

A program először a hatályos NJT-forráshoz kapcsolt külön jelmagyarázatot keresi,
majd ennek hiányában a tervlap beágyazott jelmagyarázatát. A jelentésben mindkét
forrás URL-je, eredeti SHA-256 lenyomata, kiadása, települése, PDF-oldala és a
felirat/jelminta helye szerepel. Nincs országos szín- vagy vonaltípus-táblázat.
A védőövezetek, megyei övezetek és tervezett telekhatárok külön szerepet kapnak.

A natív mintákból szín, kitöltés, vastagság, szaggatás és beágyazott jelalak
olvasható ki. A pontjelek középpontját a tényleges betűkészlet-geometria adja;
ismeretlen hézagok nem zárhatók kitalált vonallal. A korlátozásminták keresése
megtörténik, de az összes korlátozás teljes telekspecifikus ellenőrzése még hiányzik.
Az OCR-képminták megőrizhetők, de önmagukban nem állítanak elő igazolt poligont.

A sikeresen feldolgozott jelmagyarázatokat tartalomellenőrzött JSON-fájlok tárolják,
HRSZ-től függetlenül, településhez és tervkiadáshoz kötve. Alapértelmezett hely:
`/tmp/telekeloiras_legends`; a `TELEKELOIRAS_LEGEND_CACHE_DIR` környezeti változóval
tartós tárhely adható meg. Eltérő forrás vagy feldolgozó esetén új ellenőrzés indul.
A sikertelen/részleges jelminta-felismerést a program nem tárolja végleges hiányként.

A jelölt övezet forrásparaméterei és teljes helyi rendelkezései a Streamlit
felületen és a JSON-ban külön, feltételes forrásadatként jelennek meg.
Ez nem jelenti a telekre alkalmazhatóságot vagy a teljes előíráslista igazolását.

## Megismételhető ellenőrzés

A `requirements.txt` Python-csomagjai és a `packages.txt` OCR-csomagja szükséges:

```sh
python -m unittest discover -v
python reference_checks.py --output work/reference-results.json --visual-images work/reference-images
python validation/check_sources.py work/reference-results.json --output work/source-checks.json
streamlit run app.py
```

A teljes regressziós csomag 134 teszt, köztük a 36 eredeti ellenőrzés és a teljes
Streamlit vizsgálati útvonal tesztje. A GitHub Actions a főágon és a főágra
nyitott pull requesteken is futtatja. Az élő ellenőrzés külön parancs: nem függ
külső szerverek aktuális elérhetőségétől az offline regresszió.

Az `--outlined` opció a görbévé alakított HRSZ-feliratokat is feldolgozza;
nagy CAD-terveken részleges, folytatható eredménnyel is zárulhat. A hiányzó
felirat vagy API-találat nem bizonyítja a telek hiányát. Az aktuális öt mintatelek
forrásait és korlátait a `validation/` eredményfájlok rögzítik; a pillanatnyi
elérhetőség és forráskiadás később megváltozhat. Az aktuális összesített eredmény
a `validation/reference-results.json` és a `validation/reference-report.md`;
a korábbi külön OCR-próba saját időbélyeggel és feldolgozólenyomatokkal szerepel.


A forrásként igazolt terveken a HRSZ-feliratok keresése automatikus: a natív
szöveg után a kitöltött és a vonalas CAD-betűk külön OCR-csoportokba kerülnek.
Az index forráshoz és algoritmushoz kötve, folytathatóan tárolódik. A pontos,
egyértelmű felirathely előzetes B besorolás alapja lehet teljes telekpoligon
nélkül is, ha a saját jelmagyarázat kódmezője és határjelei alapján a helyi
kapcsolat ellenőrizhető. Ez nem igazolja a teljes övezeti fedést vagy az
építési jogosultságot. A C kategóriához mentett kép forrásáttekintés, nem
lokalizált telek. Az alternatív KÉSZ-ek területi hatályát a program nem
feltételezi; történeti tanulmányból nem állapít meg hatályos övezetet.

### Elkülönített, valódi képes AI-próba

A `vision_trial.py` a meglévő OpenAI képbemeneti kérésépítőt használja,
a Streamlit és a helyi felismerés eredményének módosítása nélkül. A három
próba sorrendje Miskolc 4755/11, Tiszaújváros 2200/8, Komádi 1558.
A modell bemenete kizárólag a keresett HRSZ, az eredeti, utólagos jelölések
nélküli tervkivágat, a terv saját teljes jelmagyarázatlapja és nagyított
jelmintái. A helyi övezeti eredmény külön `control.json`; nem része a kérésnek.
A kivágat helyét a korábbi helyi helyazonosítás szolgáltatja: ez a próba
**a megtalált környezet értelmezését**, nem a teljes országos HRSZ-keresést méri.
A modell a képen látható eredeti feliratokat természetesen olvashatja.

A források friss ellenőrzése és az eredeti PDF-ek gyorsítótárba töltése:

```bash
python reference_checks.py --output work/reference-results.json
python validation/check_sources.py work/reference-results.json --history-report validation/miskolc-legend-history.json --output work/source-check-results.json
python vision_trial.py prepare --report work/reference-results.json --output work/vision-trial
```

A `prepare` nem küld adatot, nem hív API-t. A forrás-PDF-ek SHA-256 egyezését,
a hatályosság korábbi ellenőrzését, a saját jelmagyarázat kötését és a nyers
kivágat egyezését ellenőrzi. `manifest.json` forrásokat, oldalakat, képméreteket,
kép-/kéréslenyomatokat és költségbecslést tartalmaz; képek és `request.json`
a három almappában. A `work/` Git által figyelmen kívül hagyott munkaterület;
API-kulcsot ide vagy a repóba sem szabad fájlba írni.

A 2026-10-10-i környezetellenőrzésben nincs API-kulcs vagy konfigurált OpenAI
kapcsolat. **Valódi AI-hívás nem történt.** Kulcs jelenléte sem bizonyít
működő API-hozzáférést: ezt csak a tényleges, engedélyezett kérés eredménye
igazolhatja. A ChatGPT-előfizetés önmagában nem API-hozzáférés.

Valódi futtatás csak a felhasználó külön pénzügyi és képküldési jóváhagyása,
valamint biztonságos környezeti `OPENAI_API_KEY` beállítása után:

```bash
python vision_trial.py run work/vision-trial/miskolc --approve-paid-call --max-estimated-usd 0.01
python vision_trial.py run work/vision-trial/tiszaujvaros --approve-paid-call --max-estimated-usd 0.01
python vision_trial.py run work/vision-trial/komadi --approve-paid-call --max-estimated-usd 0.01
```

A kapcsoló a már megadott jóváhagyás technikai rögzítése; nem helyettesíti
azt. A három parancs három külön hívás. Modell:
`gpt-4.1-mini-2025-04-14`, Responses API, `store=false`, legfeljebb 1200
kimeneti token, átirányítás és automatikus újrapróbálás nélkül. A `store=false`
nem állítja az API minden szolgáltatói adatmegőrzésének hiányát.
Egy már megkísérelt próbamappában a program nem indít újabb hívást.

Az [ellenőrzött hivatalos modellár](https://developers.openai.com/api/docs/models/gpt-4.1-mini)
$0.40/millió bemeneti és $1.60/millió kimeneti token. A három elkészített
kép/case, képenként legfeljebb 1536 patch × 1.62, 4096 szöveg-/sématoken
becsült tartalék és 1200 kimeneti token alapján **kb. $0.00655/telek,
összesen $0.01964** a konzervatív becslés. Nem garantált számlázási plafon;
a `--max-estimated-usd` becslési kapu, nem szolgáltatói költségkorlát.
Adó/árfolyam, ismételt próbák és jövőbeli árváltozás nincs benne.

A `result.json` megőrzi a független modellválaszt, a helyi kóddal való egyezést
vagy eltérést, a bizonytalanságokat, a határ- és jelmagyarázat-érvelést,
futásidőt, tokenhasználatot és a tokenekből számolt költséget. Hiba esetén
nem szimulál választ vagy nulla költséget. Megállapítható, hogy a modell
jobban olvassa-e az apró/kör alakú feliratokat, felismeri-e a hiányzó sarkokat,
megkülönbözteti-e a saját jelmagyarázat szerinti vonalakat, és indokoltan
visszautasít-e egy bizonytalan besorolást. A válaszokat az eredeti képeken
embernek is ellenőriznie kell; a helyi eredménnyel való egyezés **nem
pontossági mérőszám és nem jogi igazolás**. A három B kontrollhoz nincs
függetlenül igazolt A referencia; három példa országos pontosságot sem mér.
Az AI-próba soha nem ad A minősítést vagy igazolt építési előíráslistát.
