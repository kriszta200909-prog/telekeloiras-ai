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

### Ingyenes, helyi multimodális AI-vizsgálat

A projekt kizárólag ingyenes megoldásokat használ. A korábbi OpenAI API-próba
futtatása és a Streamlit opcionális fizetős adaptere **letiltva**, kulcs vagy
régi engedélyező kapcsoló mellett is. Nincs API-kulcs-, előfizetés- vagy
bankkártyaigény. A `vision_trial.py prepare` továbbra is csak helyi,
forráslenyomattal ellenőrzött képbemenetek előkészítésére használható.

Az alkalmazásban válassz települést és HRSZ-t, majd az „Ingyenes helyi
AI-vizsgálat” panelen válassz modellt. Ha szükséges, az „Ingyenes helyi modell
telepítése” gomb letölti a nyilvános súlyokat és elkülönített CPU-futtatókörnyezetet
készít. A „Tervrészlet és jelmagyarázat elemzése ingyenes AI-val” gomb indítja
az elemzést. Ehhez nem kell Python-programot írni vagy térképet kézzel kivágni.
A panel csak azonosított tervkörnyezet és ellenőrzött saját jelmagyarázat esetén
indíthat képelemzést; ismeretlen telekhelyet nem talál ki.

Támogatott nyílt súlyú modellek:

- [Qwen3-VL-2B-Instruct](https://huggingface.co/Qwen/Qwen3-VL-2B-Instruct):
  Apache 2.0; 2,13 milliárd paraméter, kb. 4,3 GB BF16 súly. CPU-n is próbálható,
  legalább 8 GB rendszermemória ajánlott a használt képmérethez.
- [SmolVLM-500M-Instruct](https://huggingface.co/HuggingFaceTB/SmolVLM-500M-Instruct):
  Apache 2.0; 507 millió paraméter, kb. 1 GB BF16 súly. Kisebb memóriaigény,
  de az apró tervfeliratok felismerése kísérleti.

A telepítő rögzített modellrevíziót használ; futtatáskor a súlyok SHA-256
lenyomatát ellenőrzi. A Hugging Face csak nyilvános modellfájlok letöltésére
szolgál. A következtetés offline: `local_files_only=True`,
`trust_remote_code=False`, `HF_HUB_OFFLINE=1`, `TRANSFORMERS_OFFLINE=1`.
A telek és a tervképek nem kerülnek külső AI-szolgáltatóhoz. A modellek
letöltött kártyája megőrzi a licencmegjelölést; a súlyokat nem tesszük GitHubra.
A helyi futtatás meglévő gépi erőforrást és a letöltés hálózati forgalmat használ.

A modell kizárólag az eredeti, utólagos kontrolljelölések nélküli tervrészletet,
a saját teljes jelmagyarázatlapokat, a nagyított jelmintákat és a keresett HRSZ-t
kapja. Nem kap elvárt övezetet, helyi OCR-eredményt vagy kontrollkódot.
A kontroll csak a válasz után kerül összehasonlításra. A nyers válasz, képlenyo-
matok, futásidő, memóriaigény és bizonytalanságok megmaradnak. Hibás HRSZ,
rossz JSON-típus vagy csonka válasz nem válhat övezeti eredménnyé.
**A modellvélemény nem ad A minősítést vagy teljes igazolt előíráslistát**, és
nem módosítja a működő helyi OCR-/geometriai felismerést.

Az országos telek–tervlap megfeleltetés továbbra is külön feladat: a modell a
már felismert környezet értelmezését ellenőrzi. Ismeretlen telekhelyhez még
hivatalos vagy jogszerű, ellenőrizhető helyazonosítás kell. A saját jelmagyarázat
és a hatályos HÉSZ ellenőrzése AI-válasz esetén is szükséges.
