# Hivatalos források és saját jelmagyarázatok – 2026. október 10.

**A fő cél még nem teljesült: 0/5 teleknek van teljesen bizonyított, hatályos építési előíráslistája.**

151/151 offline automatikus teszt sikeres, beleértve a 36 eredeti tesztet és a
Streamlit vizsgálati útvonalát. Az öt mintatelek élő forrásvizsgálata után
222/222 forrásalapú ellenőrzés is sikeres: eredeti PDF-bájtok, NJT-források,
időállapotok, jelmagyarázat–tervlap kötés, feldolgozólenyomatok,
bizonyítottsági állapotok és a miskolci paraméterkód feloldása.
Ez a forráskezelés helyességét ellenőrzi; nem állít sikeres telekbesorolást.

**3/5 teleknek van azonosított tervlapi helye és előzetes B övezeti eredménye; A besorolás: 0/5.**

| Telek | Tervlapi HRSZ-hely | Övezet | Minősítés | Kép | Fennmaradó akadály |
|---|---|---|---|---|---|
| Tiszaújváros 2200/8 | igen | Gip/3 | B | [tervrészlet és saját jelmagyarázat](tiszaújváros-2200-8.png) | Teljes telek- és övezeti fedés nincs igazolva. |
| Budapest XII. kerület 8448/46 | nem | nem azonosítható | C | [nem lokalizált forrásáttekintés](budapest-xii.-kerület-8448-46.png) | Egyik hatályos KÉSZ-ben sincs igazolt felirathely; engedélyezett OÉNY WMS-próba térkép helyett hibaoldalt adott. |
| Komádi 1558 | igen | Lke/1.2 | B | [tervrészlet és saját jelmagyarázat](komádi-1558.png) | Teljes telekpoligon és teljes övezeti fedés nincs igazolva. |
| Gersekarát 034/15 | nem | nem azonosítható | C | [nem lokalizált forrásáttekintés](gersekarát-034-15.png) | Nincs pontos OÉNY-találat vagy igazolt mai tervi hely; saját határjelmagyarázat nem egyértelmű. |
| Miskolc 4755/11 | igen | Gipe-60.63.5 | B | [tervrészlet és saját jelmagyarázat](miskolc-plan-context.png) | Nyitott övezeti csatlakozások és eltérő útkitöltés; teljes fedés nem igazolt. |

A C képek az ellenőrzött forrás első PDF-oldalát mutatják „nem lokalizált”
jelzéssel. Nem bizonyítják az adott telek helyét. A pontos OÉNY-találat és az
igazolt tervlapi hely külön állapot. Teljes bizonyított előíráslista: **0/5**.

Az eljárás a kitöltött és a vonalas CAD-betűket külön csoportosítja, a forgatott
lapokon megjelenítési koordinátákat használ, és az indexet az eredeti dokumentum
és a futó algoritmus lenyomatához köti. A folytatható index lemezre is mentődik.
A pontos feliratot három felbontásban ellenőrzi. A saját jelmagyarázatban a
kódmező körvonalát és osztóvonalát is megkeresi: a felirat önmagában nem elég.
A helyi kapcsolatteszt a B kategóriához nem igényel teljes telekpoligont;
sem a 4 pontos HRSZ-környezet, sem a 9 mintapont nem válik telekgeometriává.

A mintakeret nem térképi határ. A javítás geometria alapján szűri a színes
mintát körülvevő keretet, megtartja a fekete szimbólumokat, és zárt jelalakot
nem illeszt nyitott vonalra. Zárt alakzatot puszta színazonosságból nem azonosít.
A terv eredeti határjelei változatlanok. Vonalakat, sarkokat és lapszélt nem
köt össze nem igazolt folytonossággal. Tiszaújvárosnál a kivágásban nincs
illeszkedő natív határszakasz: a raszteres, saját stílusú jelvizsgálat ad helyi
jelöltkapcsolatot, nem zárt övezetpoligont. B kategória, nem kalibrált valószínűség.

Független utcahálózati/épületalaprajzi illesztés még nincs igazolva. A helyi
utcafeliratok és szomszédos HRSZ-ek OCR-nyomai ellenőrizendő környezeti adatok.
Az eredménytelen teleknél a program nem választ közeli övezeti feliratot.
A budapesti északi és déli KÉSZ-t külön, hatályos hivatalos forrásként ellenőrzi;
csak egyetlen pontos, teljes keresésben megerősített találatnál vált rendeletet.
Többértelmű vagy részleges alternatív keresés nem ad A besorolást.

Az automatikus jóváhagyási ellenőrzés elutasította a pontos telekkoordinátás
Nominatim-próbát és az OÉNY saját `hrsz/wms` szolgáltatásának GetMap-próbáját.
Indoka: pontos, telekből származtatott helyadat külső célhelyre továbbítása
nincs külön jóváhagyva. A kérések nem mentek ki; WMS-válaszhibát nem állítunk.
Az OÉNY hivatalos frontendjében a `foldreszlet`, `felirat_kat` és `epulet`
rétegeket azonosítottuk. Az OÉNY-koordinátás kérés engedélye fennmaradó akadály.

A forrásjegyzék a gersekaráti önkormányzati tanulmány pontos HRSZ-említéseit
és azok PDF-oldalát megőrzi. Ezek történeti nyomok, nem mai telekhely vagy
hatályos övezet bizonyítékai; a forrásbájtok külön ellenőrzöttek.

## Helyi vizuális vizsgálat – 2026. október 10.

A miskolci képfeldolgozó útvonal az igazolt tervlapi telek környezetét vizsgálja.
A többi tervnél az önálló, pontos felirathelyet használó B útvonal működik. A telek EOV-geometriáját a terv saját GEO-illesztésével
helyezi a képre, és a telekbelsőben levő pontos HRSZ-felirattal ellenőrzi.
A környező telekfeliratok pozícióit kigyűjti; ezek **szomszédsági jelöltek**,
nem bizonyított szomszédos földrészletek. A környezet utcafeliratait is keresi;
a miskolci kivágásban teljes utcanevet nem sikerült automatikusan kiolvasni.
Önálló kataszteri–tervi utcahálózat- és épületalaprajz-egyeztetés még nincs.

A saját hivatalos jelmagyarázat színeiből és jelalakjaiból olvas akadályokat,
és valamennyi helyi övezeti felirat kapcsolatát több, a telekbelsőben levő
ponthoz vizsgálja. A natív pontozott jel forrásbeli sorrendjét is ellenőrzi:
a fehér képpontok miatt nem lehet átmenni a saját jelmagyarázat szerinti
határon. Más sorok végét, sarkokat és a képkivágás szélét nem köti össze.
Fekete, színes, folytonos és pontozott helyi minták nem országos alapértékek.

**Miskolc 4755/11:** a `Gipe-60.63.5` felirat 5/9 telekbelső ponthoz ad
helyi, akadálymentes jelöltkapcsolatot. A túloldali `Gksz-71.62.6` felirat
0/9 ponthoz: a forrásból felismert határ blokkolja. A Gipe-felirat ténylegesen
**a vizsgált teleken kívül** található. A kapcsolata ezért **valószínű,
nem bizonyított**; nem helyettesíti a teljes telek övezeti hozzárendelését.
A vizuális állapot a Streamlitben az igazolt geometriai besorolástól külön
jelenik meg, és nem teszi alkalmazhatóvá a feltételes előírásokat.

[Képi bizonyíték](miskolc-plan-context.png): cián a vizsgált telek; zöld a
saját jelmagyarázathoz illesztett eredeti szakasz; lila a felirat és a
jelöltkapcsolat; kék kör az eredeti nyitott csatlakozás. Ezek az alkalmazás
magyarázó jelölései. A kép alatt a hivatalos jelmagyarázat eredeti kivágásai
láthatók. Az eredeti tervrészlet és a magyarázó ábra külön SHA-256 lenyomatot
kap; a jelmagyarázat forrásbájtjait megjelenítés előtt ellenőrzi.
A meglévő képi bizonyítékot frissítettem, nem új tervváltozatot hoztam létre.

**AI és költség:** a helyi PDF-/Tesseract-/numpy/Pillow-felismerés kontrollként
megmaradt. A fizetős OpenAI-adapter és a korábbi API-próba futtatása letiltva,
régi engedélyező kapcsoló és kulcs mellett is. Külső AI-hívás és API-költség: 0.
A nyílt súlyú Qwen3-VL 2B és SmolVLM 500M modellekkel már valódi, helyi,
offline képelemzést végeztünk mindhárom lokalizált telken. A nyers válaszok,
modellek/súlyok lenyomatai, képbemeneti lenyomatok és erőforrásmérések a
[helyi modelljegyzőkönyvben](local-vision-results.json) találhatók.
A modellvélemény nem geometriai vagy jogi igazolás; a részletes összevetés
alább szerepel. A meglévő gépen futtattunk; új előfizetés, fizetős szolgáltatás
vagy pénzügyi kötelezettség nem keletkezett.

**Genspark:** a [Team/Enterprise hivatalos súgója](https://www.genspark.ai/helpcenter/team-enterprise-plans)
API-kulcsok adminisztrációját említi, tehát nem állítjuk, hogy semmilyen API
nem létezik. A [connector-dokumentáció](https://www.genspark.ai/helpcenter/connectors-and-integrations)
azonban külső szolgáltatások Gensparkon belüli használatát írja le. A vizsgált
hivatalos oldalakon nem találtunk dokumentált, külső alkalmazásból hívható
képelemző végpontot, hozzá tartozó sémát, díjszabást és a felhasználó konkrét
előfizetésének jogosultságát. Genspark-előfizetés ezért nem tekinthető igazolt
API-hozzáférésnek; webes munkamenetből visszafejtett nem hivatalos adapter
nem készült. Hivatalos API-szerződés/dokumentáció nélkül ez az integráció blokkolt.

**A/B/C eredmény:** a Streamlit és a letölthető bizonyíték-JSON ugyanazt a
kategóriát tartalmazza. A: igazolt geometriai besorolás; B: vizuálisan nagy
valószínűséggel azonosított besorolás, feltételes előírásokkal; C: nem
azonosítható. A B megnevezés kategória, nem kalibrált számszerű valószínűség.
A végső élő ellenőrzés a végleges forráskód lenyomatát rögzíti.
A vizuális kód közvetlenül a forrásolt paraméter- és rendelkezésolvasóhoz
kapcsolódik; nem egy korábbi közeli geometriai jelöltet használ. Az eredeti
geometriai azonosítást nem módosítja, az építési jogosultságot nem igazolja.
Miskolc, Tiszaújváros és Komádi B; Budapest XII. és Gersekarát C; teljes bizonyított előíráslista továbbra is 0/5.

Az [e-közmű hozzáférési feltételeit](https://www.e-epites.hu/gyik?temakor=177)
október 10-én újra ellenőriztem: KAÜ-azonosításhoz kötött. Új, igazolt,
hitelesítés nélküli kataszteri adatkapcsolat nem került elő; az OÉNY és a
hatályos NJT-források maradnak az ellenőrzött alternatívák.

A vizuális módszer országos övezeti kódot, települést vagy HRSZ-eredményt
nem éget be. Jelenlegi korlátja az igazolt, koordinátával összekapcsolható
tervlapi telekgeometria; forgatott lap vagy hiányzó illesztés esetén elutasítja
a következtetést. A másik négy telek teljes vizuális egyeztetése és a teljes
telekspecifikus építési előíráslista továbbra sem kész. A bizonyítás akadályait
a korábbi tervi geometriai audit változatlanul megőrzi.

## Hivatalos GIS-források – élő vizsgálat

A program a nyilvános [Lechner INSPIRE-katalógust](https://inspire.lechnerkozpont.hu/geonetwork/srv/eng/catalog.search)
szabványos CSW GetRecords lapozással dolgozza fel. A három lap összesen
286 külön azonosítójú rekordot tartalmaz; ismétlődő vagy hiányos lapozás nem
minősül teljes keresésnek. A közzétett szolgáltatáscímeket követi, rejtett
végpontok találgatása és hitelesített munkamenetek megkerülése nélkül.

- A kataszteri `CP:CP.CadastralParcels` WFS GetCapabilities működik, de saját
  rétegleírása **Mesterszállás mintaterületét** nevezi meg. Ez nem igazolja az öt
  mintatelek országos kataszteri ellátottságát. A CSW absztrakt országos adatról
  szóló általános mondata nem írhatja felül a tényleges szolgáltatás területét.
- A `LU.NGMHU` LandUse2023 WMS működik; ez meglévő területhasználati térképkép,
  nem hatályos helyi építési övezetek igazolt vektoros szolgáltatása.
- A vízügyi katalógusban közzétett WFS-cím HTTP 400-at ad. Ez elérési hiba,
  nem annak bizonyítéka, hogy a telek nem érintett vízügyi korlátozásban.
- Az országos [örökségvédelmi ZIP](https://inspire.lechnerkozpont.hu/inspire/cultural_heritage/cultural_heritage.zip)
  ténylegesen letölthető: hat SHP-réteg, összesen 7452 rekord. A hivatalos,
  azonosítóhoz kötött ISO-metaadat igazolja a nyilvános CC BY 4.0
  újrafelhasználást. Forrás: Lechner Tudásközpont; a TelekElőírás AI a
  térbeli metszést számítja újra. [Licenc](https://creativecommons.org/licenses/by/4.0/).
  Az eredeti ZIP-ben **6 NULL geometria és 2 érvénytelen poligon** található.
  Ezeket a program megszámolja, nem javítja és nem használja bizonyítékként.
  A pillanatkép nem teljes védettségi/korlátozási nyilvántartás.
- Tiszaújváros hivatalos térképnézője elérhető, de a betöltő oldal nem közöl
  igazolt hatályos övezeti vektorletöltést. A felület belső eseménykezelőjét
  a program nem kezeli dokumentált nyilvános GIS API-ként.
- A XII. kerületi MINERVA hivatalos belépőoldala HTTP 503 hibát ad.
- Miskolc, Komádi és Gersekarát hivatalos oldalainak vizsgálata nem adott
  a kért telkekhez hatályos HÉSZ-hez kötött, igazolt övezeti vektorforrást.
  Ez a vizsgált források eredménye, nem országos szolgáltatások nemlétezési állítása.

Az [E-TÉR hivatalos leírása](https://data.lechnerkozpont.hu/szolgaltatas/elektronikus-tersegi-tervezest-tamogato-rendszer-e-ter)
részben nyilvános rendszert ír le. A belépőoldal betöltődik; a nyilvános
JavaScript projektazonosítóhoz kötött WFS-útvonalat is tartalmaz, de ez nem
igazol szabadon használható, hatályos miskolci övezeti végpontot. A belső API-ra
és kitalált projektazonosítókra nem épül integráció.

Az [e-közmű hivatalos GYIK](https://www.e-epites.hu/gyik?temakor=177)
KAÜ-azonosítást, a vektoros közműadatokhoz kamarai jogosultságot és
tervezéstámogatási kérelmet ír le. A nyilvános belépőoldal elérhető, de
ellenőrzött, díjmentes és hitelesítés nélküli vektoros API-t nem találtam.
A közműkorlátozások teljes vizsgálata ezért továbbra sem automatizált.

A GIS-feldolgozás a meglévő fej nélküli vizsgálatba és a Streamlit felületbe
került. A WMS, a WFS és a jogilag igazolt övezet külön állapot; a letöltési
SHA-256, HTTP-státusz, forráscím és licencbizonyíték megmarad. A már meglévő
hivatalos PDF és annak saját jelmagyarázata továbbra is vizsgált forrás.
Az új téradatok egyike sem ad igazolt övezeti poligont az öt mintatelekhez.
Miskolc igazolt telekpoligonja az országos örökségvédelmi pillanatkép érvényes
geometriáival nem metsződik. Ez nem igazolja a védelem hiányát a hiányos
geometriák és a nem teljes jogi adatkapcsolat miatt. A másik három elérhető
OÉNY-körvonal vizsgálata csak jelöltgeometriás előszűrés. Gersekarát esetén
telekgeometria hiányában a metszés nem végezhető el.

## Saját hivatalos jelmagyarázat

A program a hatályos NJT-rendelethez hivatkozott külön jelmagyarázatot keresi,
ennek hiányában a terv beágyazott jelmagyarázatát. A saját felirat mellett
talált jelből olvassa ki a színt, kitöltést, vonalvastagságot, szaggatást és
a natív jelalak lenyomatát. Nincs országos szín- vagy vonaltípus-tábla.

| Település | Jelmagyarázat forrása | Automatikus övezethatár-jelminta |
|---|---|---|
| Tiszaújváros | [hivatalos terv](https://njt.jog.gov.hu/document/d9/d95fLL_EJR_81697536-rendelet_mell_klet-1.pdf), 2. PDF-oldal | két felbontású OCR-felirat és natív rajzi minta |
| Budapest XII. | [hivatalos terv](https://njt.jog.gov.hu/document/c3/c3f0LL_EJR_99708274-20250806_D-Hegyvid_k_K_SZ_1_mell_klet.pdf), 4. PDF-oldal | natív felirat és rajzi minta |
| Komádi | [hivatalos terv](https://njt.jog.gov.hu/document/29/29e9LL_EJR_109881483-tervlap_T_3_BELTERULETI_SZAB_2025_egyben.pdf), 1. PDF-oldal | két felbontású OCR-felirat és natív rajzi minta |
| Gersekarát | [hivatalos közigazgatási terv](https://njt.jog.gov.hu/document/ff/ffecLL_EJR_55522770-2._mell_klet_H_SZ.pdf), 1. PDF-oldal | a felirat felismerhető; a hozzá tartozó minta nem igazolt |
| Miskolc | [külön hivatalos jelmagyarázat](https://njt.jog.gov.hu/document/03/0366LL_EJR_83921015-Jelmagyarazat_modositasa.pdf), 1. PDF-oldal | natív felirat és beágyazott körjel |

A jelmagyarázat és a tervlap URL-je, eredeti SHA-256 lenyomata, PDF-oldala,
kiadása és településazonosítója a JSON-ban szerepel. Az eltárolt profil más
HRSZ-hez újrafelhasználható ugyanazon tervkiadáson belül. Másik terv vagy
módosított gyorsítótár nem kölcsönözhet jelöléseket. Tartós tárhely a
TELEKELOIRAS_LEGEND_CACHE_DIR változóval adható meg.

**A jelmagyarázat feldolgozása nem egyenlő a teljes övezetpoligon vagy minden
területi korlátozás felismerésével. Az országos megoldás még nem teljes.**
A raszteres minták megőrzése elkészült; teljes raszteres határrekonstrukció,
egyes CAD-minták és az összes telekspecifikus korlátozás ellenőrzése hiányzik.

## Tiszaújváros 2200/8

A Gip/3 tényleges telekhez tartozását nem sikerült sem bizonyítani, sem cáfolni.
A korábbi színalapú adapter besorolása letiltva, mert az új, saját
jelmagyarázat szerinti határfelismerési követelményt nem teljesíti.
Az automatikus keresés a 35. PDF-oldalon három felbontásban azonosította a
2200/8 HRSZ-t. A saját kódmező alapján két felbontásban Gip/3-at olvasott,
a felirathelyhez 9/9 helyi kapcsolatot vizsgált. Ez B kategória: nem igazolt
telek- vagy övezetpoligon. A kivágásban nem talált illeszkedő natív határszakaszt;
ennek hiánya sem övezetpoligont, sem a teljes telek besorolását nem bizonyítja.

A felhasználó által kérdezett **Gip/3 övezet forrástábláját külön ellenőriztem**,
az algoritmus nem kapta meg ezt előre ismert telekbesorolásként:
szabadon álló beépítés; legnagyobb beépítettség 30%; legkisebb telekterület
10 000 m²; legkisebb zöldfelület 25%; legnagyobb épületmagasság 12,50 m.
A 2. lábjegyzet technológiai indokoltság esetére eltérést enged.
Forrás: [hatályos NJT-rendelet](https://njt.jog.gov.hu/jogszabaly/2018-11-SP-5Y1228)
2024.10.10. időállapot, [paramétertábla](https://njt.jog.gov.hu/document/af/afafLL_EJR_80774847-2._mell_klet.pdf)
2. PDF-oldal, lábjegyzet a 3. PDF-oldalon.
Ezek az övezet forrásadatai, alkalmazhatóságuk a 2200/8 telekre nem igazolt.

## Miskolc 4755/11

A hivatalos [terv](https://njt.jog.gov.hu/document/b4/b45dLL_EJR_127797597-Belteruleti_szabalyozasi_tervlapok_modositasa.pdf)
31. PDF-oldalán a pontos HRSZ és a zárt natív kataszteri CAD-telek együtt
azonosítható. A saját jelmagyarázat megnevezi ezt a rétegtípust; nyomtatott
színe/vastagsága eltér a jelmagyarázat mintájától, ezért a kataszteri adatot
a név szerint azonosított forrásréteg és a geometriavizsgálat igazolja.
Az övezethatárnál nincs ilyen névalapú kivétel: a saját körjel-alak egyezése kötelező.

A hatályos saját jelmagyarázat „Építési övezet, övezet határa, jele” sorában
a piros beágyazott körjel szerepel. A piros folytonos, 0,96 PDF-pont
vastagságú vonal külön sor szerint **szabályozási vonal**, ezért nem
használható fel övezeti sarok önkényes lezárására. Más településnél a
folytonos vagy szaggatott határ szerepét annak saját jelmagyarázata adja meg.

A natív körjelek tényleges középpontját a beágyazott betűkészletből számítja
a program. 3314 jel került feldolgozásra. Feliratok alatti hiányokat nem zár
kitalált vonallal, és a saját szabályozási/területi mintákat is vizsgálja.
A teljes, zárt övezetpoligon és a teljes telek kapcsolata nem igazolt.
A fő cél tehát ebben a fejlesztési lépésben sem teljesült.
A **Gipe-60.63.5 csak tervlapi jelölt**, nem igazolt besorolás.

A jelölt kódot az alkalmazás saját [hivatalos paraméterjelmagyarázatából](https://njt.jog.gov.hu/document/f3/f327LL_EJR_124216758-Param_terek_magyar_zata.pdf)
oldotta fel. A 60 nem 60%-os beépíthetőséget jelent:

| Kódpozíció | Forrás szerinti előírás | Érték |
|---|---|---|
| 1: 6 | legnagyobb épületmagasság | 12,5 m |
| 2: 0 | beépítési mód | adottságtól függő |
| 3: 6 | legnagyobb beépítettség | 50% |
| 4: 3 | legkisebb zöldfelület | 25% |
| 5: 5 | kialakítható legkisebb telekterület | 1200 m² |

Emellett a [2026.09.26. időállapotú rendelet](https://njt.jog.gov.hu/jogszabaly/2022-38-SP-5Y1070)
24., 25., 33. és 36. §-ából 20 teljes forrásbekezdés betöltődött, kiadás- és
szöveglenyomat-ellenőrzéssel. A teljes közművesítés, zöldfelületi feltételek,
rakodás, technológiai eltérések és hivatkozott országos szabályok feltételei
megmaradnak. A telekre/ügyre alkalmazhatóság külön igazolandó.


### Most ellenőrzött fejlesztések és konkrét hiányok

A többoszlopos jelmagyarázat hosszú feliratai mellett a következő oszlop
mintája tévesen kétértelművé tette az útterület-jelöléseket. A javítás legalább
három, ugyanebben a saját jelmagyarázatban igazolt, egy oszlophoz tartozó
natív sor alapján állapítja meg a minták oldalát. Egy magányos, kétoldali
mintát továbbra sem fogad el. A kétsoros felirat teljes magasságával dolgozik,
ezért nem cseréli le a magas útterületmintát a szomszéd oszlop mintájára.

A program közös, tényleges metszéspontokon csomópontosított gráfban dolgozza
fel a saját jelmagyarázattal egyező natív folytonos/szaggatott szakaszokat,
a natív pontsorokat és az igazolt út-/területkitöltések határait. Korábban
a külön feldolgozás miatt a vegyes határtípusok nem tudtak közös poligont
alkotni; ez javítva. Külön ellenőrzött teszt igazolja a PDF kifejezett
`h` záróparancsának natív szakaszként való kiolvasását. Hiányzó
zárószakaszt nem pótol a program. Ha egy ténylegesen felismert natív
határjel alakja vagy pontsora nem támogatott, a zárt natív vonalas terület
sem adhat igazolt besorolást: a fel nem dolgozható határ nem hagyható figyelmen kívül.
A lap széle és a feliratmaszk nem zárhat le övezetet.

### Konkrét tervlapi csatlakozásvizsgálat

A 31. PDF-oldal / 20-4 szelvény eredeti bájtjaiból újramért, a telekhez
legfeljebb két natív jelismétlési távolságra található nyitott végpontok:

| Nyitott végpont, PDF-pont (x; y) | Legközelebbi igazolt forráshatárig mért rés, PDF-pont | EOV-távolság |
|---|---|---|
| 309,627815; 476,334748 | 0,003131 | 0,004417 m |
| 312,674873; 478,988520 | 0,600543 | 0,847242 m |
| 400,342294; 468,207266 | 0,007697 | 0,010859 m |
| 313,921548; 478,230551 | 0,008733 | 0,012320 m |

A legközelebbi forrásgeometria nem feltétlenül a hiányzó övezeti csatlakozás
másik oldala. Ezek **résmérések**, nem bizonyított vonalösszetartozások.
Az eredeti pontok, a legközelebbi forráspont és a teljes szakasz WKT-je a
`reference-results.json` miskolci `closure_audit.topology` mezőjében vannak.
Az önálló forrásellenőrzés ismételten megnyitja az eredeti hivatalos PDF-et,
és újraszámítja a végpontokat, a távolságokat és a poligonzárást. A saját
hatályos jelmagyarázattal igazolt gráf nem ad feliratozott övezetpoligont
a teleknél; ez **nem cáfolja** a Gipe-60.63.5 besorolást, hanem a bizonyítás
sikertelenségét dokumentálja.

Az illesztési maradék 0,0701385 m, a konzervatív abszolút bizonytalanság
1,0701385 m. A telekhatár és az övezeti jelek ugyanazon natív PDF-rendszerben
vannak. A közös invertálható affine transzformáció megőrzi a metszéseket és
a fedési kapcsolatokat; ezért koordinátaeltolással nem lehet hitelesen
bezárni ezeket a forrásréseket. A forrásellenőrzés az invertálhatóságot és
a telekkoordináták visszaalakítását is ellenőrzi. A bizonytalansági sáv nem
felhatalmazás a telek más övezetbe történő átmozgatására.

**A fennmaradó akadály:** nincs bizonyított, jelenlegi saját jelmagyarázattal
értelmezett zárt övezeti terület, amely a teljes telket fedi. A natív PDF a
pontjel-sorozatok között nem ad közös sarokazonosítót vagy övezeti
vektortopológiát; a déli kitöltés hatályos jelváltozatként való használata
sem igazolt. A rendelkezésre álló PDF-ből ezek feltételezés nélküli pótlása
nem sikerült. Az akadály feloldásához a csatlakozásokat és a jelváltozatot
igazoló, hatályos hivatalos geometriára vagy egyértelmű tervi megfeleltetésre
van szükség. A város [hivatalos szabályzati oldala](https://www.miskolc.hu/varoshaza/onkormanyzat/strategiak-koncepciok/miskolc-megyei-jogu-varos-epitesi-szabalyzata)
az NJT-rendeletre hivatkozik; az oldalon talált partnerségi és korábbi
tervdokumentumok nem helyettesítik ezt a hatályos bizonyítékot.

A hatályos, külön jelmagyarázat közúti mintája RGB (1; 0,761; 0), miközben
a tervben a korábbi RGB (1; 0,796; 0,31) szerepel. Ennek forrását megtaláltam:
az [ugyanezen rendelet 2023.02.01. időállapotában](https://njt.jog.gov.hu/jogszabaly/2022-38-SP-5Y1070.0)
hivatkozott [saját korábbi jelmagyarázat](https://njt.jog.gov.hu/document/80/8042LL_EJR_40048648-1_MELLEKLET.pdf),
2. PDF-oldal, ugyanilyen megnevezésű közúti minta. A színérték három tizedesre
kerekítve egyezik. Ez a történeti jelváltozat eredetét igazolja; a hatályos
tervlapra való automatikus megfeleltetést és a teljes telekbesorolást még nem.
A részletes receiptek a [miskolc-legend-history.json](miskolc-legend-history.json)
fájlban vannak. Régi építési előírásokat nem alkalmaz a program.

A területi audit az építési vonal, védelmi, tilalmi és közműjelölések saját
mintáit vizsgálja. Azonos megjelenésű, eltérő jelentésű minták esetén minden
lehetséges saját feliratot megőriz. A kitöltés befoglaló téglalapja helyett
annak tényleges poligonját metszi a telekkel. A nem támogatott jelek és a
jogi védőtávolságok külön hiányként szerepelnek a JSON-ban és a Streamlitben.
Ez továbbra sem teljes területi korlátozásvizsgálat.

![Hivatalos tervkivágat az alkalmazás magyarázó jelöléseivel](miskolc-plan-context.png)

A kivágat a fent hivatkozott terv 31. PDF-oldaláról, a 20-4 szelvényről készült.
A tényleges kivágási koordináták és raszterezési lépték az aktuális JSON
`visual.clip_pdf` és `visual.scale` mezőiben szerepelnek. A magyarázó rétegek
nem a hivatalos terv módosításai.
A 4755/11 és 4755/10 telekfelirat, a közöttük húzódó telekhatár és a
Gipe-60.63.5 felirat külön látható. Ez a forrásképet dokumentálja,
a teljes geometriai besorolást önmagában nem igazolja.

A friss helyi HTML-forrásból 253 témabeli találat, 186 külön hivatkozható
rendelkezés megőrződött. Az öt paramétert, a 20 kapcsolódó bekezdést és a
kinyert helyi forrásleltárat a [miskolc-source-clauses.md](miskolc-source-clauses.md)
fájl tartalmazza. Az általános és más területek rendelkezéseit is tartalmazó
leltár nem tekinthető a 4755/11 teljes alkalmazható előíráslistájának.

## A további telkek

Budapest XII. 8448/46: pontos OÉNY-találat; az északi 26/2020. és a déli
36/2021. KÉSZ hatályos tervét is külön megkereste az algoritmus. Egyik teljes
OCR-indexben sincs igazolt pontos felirat. Ez nem a telek hiányának bizonyítása; nincs
igazolt telekhatár–övezet kapcsolat. A MINERVA-adapter besorolása saját
jelmagyarázat szerinti vonalosztályozás nélkül letiltva.

Komádi 1558: pontos OÉNY-találat, automatikus háromfelbontásos tervlapi
felirat az 1. PDF-oldalon. A saját kör alakú kódmező ellenőrzése után Lke/1.2
olvasható; 9/9 helyi feliratkapcsolat, B kategória. Teljes telek- és övezetpoligon
nincs igazolva; az esetleges paraméter-/előíráslista alkalmazhatósága feltételes.

Gersekarát 034/15: az OÉNY pontos keresése nem ad találatot, a 2019-es
közigazgatási terv OCR-próbája sem igazol pontos feliratot. Ez nem bizonyítja
a telek hiányát vagy átnevezését. A 2._mell_klet_H_SZ.pdf fájlnév ellenére
valódi tervlap; a korábbi téves mellékletválasztás javítva.
Az önkormányzati módosítási tanulmányban a 034/15 pontos említései bekerültek
a forrásjegyzékbe: nyugati nyúlvány, napelempark, 13,32 ha történeti adat.
A tanulmány nem hatályos besorolási bizonyíték, mai telekhelyet nem igazol.

## Megismételhetőség

```sh
python -m unittest discover
python reference_checks.py --output work/reference-results.json --visual-image work/miskolc-plan-context.png
python validation/check_sources.py work/reference-results.json --history-report validation/miskolc-legend-history.json --output work/source-check-results.json
python reference_checks.py --outlined --output work/reference-outlined-results.json
```

reference-results.json: aktuális öttelekes vizsgálat és forrásreceiptek.
source-check-results.json: 222 sikeres ellenőrzés, a jelentés lenyomatával.
reference-outlined-results.json: külön HRSZ-feliratpróba, saját forrás- és
feliratfeldolgozó-lenyomatokkal; nem övezeti bizonyíték.
conditional-zone-parameters.json: a Gip/3 forrássor feltételes ellenőrzése.
test-results.json: helyi tesztfutás. A GitHub Actions külön futtatja a teljes
offline csomagot a meglévő PR #1-en.

## Fennmaradó külső adatfüggőség és folytatási állapot

Miskolc esetén a hatályos szabályozási tervhez hivatalosan kötött, zárt
övezeti vektorpoligon hiányzik az ellenőrzött hozzáférhető forrásokból.
Alternatíva a terv nyitott csatlakozásait és eltérő útterületi jelváltozatát
hitelesen feloldó hivatalos geometriai adat. A feltárt katalógus/WFS/WMS ezt
nem helyettesíti. Az országos GIS-övezeti azonosító és a teljes telekspecifikus
előíráslista ezért még nem kész: jelenleg forrásfelderítés és egy ellenőrzött
licencű SHP-pillanatkép metszésvizsgálata működik. A vektoros övezetadat
bekötéséhez az aktuális rendelet/tervkiadás, területi lefedettség, kódmező,
CRS, letöltési teljesség és jogi felhasználhatóság együttes bizonyítéka kell.
Nem kértem új hozzáférést, nem nyújtottam be kérelmet és nem olvasztottam
be a PR-t a főágba.

## Budapest XII. 8448/46: külön engedélyezett WMS-ellenőrzés (2026-10-10)

A felhasználó kifejezetten engedélyezte e telek EOV-koordinátáinak egyszeri
elküldését az OÉNY hivatalos WMS-szolgáltatásának helyazonosítás és tervi
összevetés céljából. Más szolgáltatáshoz nem küldtünk koordinátákat.

A [nyilvános OÉNY kereső](https://www.oeny.hu/oeny/hrsz-kereso/) célja
a helyrajzi számok térképi azonosítása. A
[hivatalos futásidejű konfiguráció](https://www.oeny.hu/oeny/hrsz-kereso/assets/env.js)
geoUrl értéke `https://www.oeny.hu/hk-geoserver`; a kereső térképi modulja
a `/hrsz/wms` végpont `hrsz:foldreszlet`, `hrsz:felirat_kat`,
`hrsz:epulet` rétegeit használja. A lekérdezés ugyanezekre a nyilvános
térképi rétegekre irányult, hitelesítés vagy hozzáférési korlátozás
megkerülése nélkül. Ez nem jelent általános adat-újraközlési licencet vagy
hiteles földhivatali telekhatár-bizonyítékot.

Az engedélyezett EPSG:23700 GetMap kérés lefutott. A válasz **HTTP 200,
Content-Type text/html, 2035 bájt**, címe **OENY Hiba**, nem PNG térképkép.
A HTTP sikerstátusz ezért nem térképi siker. A kérés nem koordinátás paraméterei, időpontja,
a teljes kapott hibaoldal és SHA-256 lenyomata a
[WMS-ellenőrzési jegyzőkönyvben](budapest-xii-wms-receipt.json) szerepelnek.
A pontos EOV-koordináták csak a helyi munkajegyzőkönyvben maradnak;
a GitHubra kerülő változat nem tartalmazza őket.
A szolgáltatás a válaszban nem közölt konkrét szerveroldali hibaokot;
hitelesítési követelmény vagy hiányzó telek ebből nem állapítható meg.
Nem történt második koordinátás kérés, sem alternatív szolgáltatásnak küldés.

**Eredmény: C.** Nincs új térképi bizonyíték, ezért továbbra sincs igazolt
megfeleltetés a telek és az északi/déli KÉSZ megfelelő tervrészlete között.
Az eddigi XII. kerületi kép csak nem lokalizált terváttekintés, nem
telekhely-bizonyíték. A továbblépéshez működő hivatalos térképkép vagy más
jogszerű, ellenőrizhető térképi helyazonosítás szükséges.
A korábbi automatikus elutasításra vonatkozó részek történeti események;
az új, kifejezett engedély alapján ez a kérés már ténylegesen lefutott.

## Ingyenes multimodális modellek valódi összevetése

Mindkét modell Apache 2.0 licencű, nyilvánosan letölthető; nem használtunk
hostolt inference API-t. Modellazonosítók és rögzített revíziók a
[nyers futási jegyzőkönyvben](local-vision-results.json). A SmolVLM 256M
licencét és 256 millió paraméterét ellenőriztük, de nem futtattuk. A Qwen2.5-VL
3B egyedi `qwen-research` licencű és kb. 7,5 GB BF16 súlyt igényelne, ezért
az általánosan újrahasználható megoldáshoz a Qwen3-VL 2B-t választottuk.

| Telek | Helyi kontroll, B | Qwen3-VL 2B tényleges válasz | SmolVLM 500M tényleges válasz |
|---|---|---|---|
| Miskolc 4755/11 | Gipe-60.63.5 | Gipe-60.63.5, HRSZ egyezik; határzártsági indoklás hibás/önellentmondó | EV, hibás HRSZ és csonka/hibás szerkezet; elutasítva |
| Tiszaújváros 2200/8 | Gip/3 | „12,5* 20 %”: építési paramétert nézett övezetnek; elutasított övezeti eredmény | SZ, hiányzó HRSZ és csonka/hibás szerkezet; elutasítva |
| Komádi 1558 | Lke/1.2 | K; a helyi forrásolvasás nem támasztja alá, nem fogadjuk el övezeti eredményként | EK2, hibás logikai mező/csonka válasz; elutasítva |

**Egyezés a helyi kontrollal: Qwen 1/3, SmolVLM 0/3.** Ez nem hiteles
pontossági arány: a három helyi B kontrollhoz sincs függetlenül igazolt A
referencia. Az előzetesen azonosított telkek száma nem nőtt: továbbra is 3/5 B,
2/5 C; teljes bizonyított előíráslista 0/5. A modellek gyakorlati előnye itt
nem bizonyított. A Qwen önállóan olvasott egyező miskolci kódot, de nem adott
megbízható telek–övezet topológiát; a hagyományos módszert nem váltja ki.

A modell a nyers, utólagos kontrolljelölések nélküli tervkivágatot, a saját
teljes jelmagyarázatot és nagyított jelmintákat kapta. A prompt a keresett
HRSZ-t tartalmazza, **nem az elvárt kódot**, nem helyi OCR-feliratlistát.
Az eredeti forrás-PDF-ekből visszaállított képek hash-egyezését külön ellenőrzés
vizsgálja. A kontrollövezet csak a válasz után kerül az összevetésbe.

**Tényleges hardver:** GPU nincs; 3 látható CPU-mag, de cgroup-kvóta szerint
2 CPU és 8192 MiB memória. A Qwen próbák 341/641/422 másodpercig tartottak,
a mért kumulatív RSS-csúcs 6623 MiB. A SmolVLM 90/78/99 másodperc,
legfeljebb 4102 MiB. A mérések részben párhuzamos forrásellenőrzéssel futottak;
nem szabványos sebességbenchmarkok. A modell súlyai BF16 formátumban futottak.
Az első float32/alapértelmezett nagy képes SmolVLM-kísérlet OOM miatt kilépett
(137); BF16 és explicit képméretkorlát mellett mindhárom próba sikeresen
lefutott. A Qwen betöltésének hiányzó torchvision-függőségét pótoltuk.

A futtató immár figyelembe veszi a tényleges CPU-kvótát. A felületi próba
legfeljebb 15 percet enged; hiba esetén a kontroll megmarad. A modell
telepítése és indítása a felület gombjaival történik, nem kell Python-kódot
írni vagy térképet kézzel feldolgozni. A gombok a gyorsítótárazott vizsgálaton
kívül vannak; ennek regressziós hibáját javítottuk. A tesztcache ismételt
futtatásokból származó szennyeződését is megszüntettük izolált tesztmappával;
a települési jelmagyarázatok és az országos OCR-cache újrahasználata megmaradt.

A hibás HRSZ, logikai mező és csonka JSON nem ad elfogadott modelljelöltet.
A helyi forrásfeliratokkal nem alátámasztott modellszöveg külön figyelmeztetést
kap. A modell zártsági állítása mellett a forrásbeli megszakadásokat is
láthatóvá tesszük, és nem állítunk igazolt övezeti poligont. A teljes jogi
előíráslista továbbra is hivatalos, hatályos forrásokra és a telek tényleges
övezeti/korlátozási kapcsolatának bizonyítására szorul.
