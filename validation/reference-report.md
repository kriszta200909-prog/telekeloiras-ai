# Országos telekazonosítás – ellenőrzött fejlesztési eredmények

A végleges öttelekes forrásvizsgálat kezdete: **2026-10-10T12:51:16.528354+00:00**.
A PR ágára közben beérkezett hibás GIS-URL és keskeny fedési rés javítását
megőriztük. Az egyesített kódon az összes teszt és a PDF-/jelmagyarázat-/
HRSZ-/képi/jogszabályi forrásellenőrzés újra sikeres. A JSON eredeti
`code_sha256` mezője az élő forrásfuttatás verzióját őrzi; a `revalidation`
mező és a forrásellenőrzés külön kódlenyomatai az aktuális újraszámítást
kötik a forrásadatokhoz. Így az eredeti vizsgálatot nem tüntetjük fel új
élő adatlekérésként.

**Tervlapi HRSZ-környezet: 3/5 → 5/5. Övezeti besorolás: 0 A, 3 B, 2 C.
Teljesen bizonyított, hatályos telekspecifikus előíráslista: 0/5.**
A fő cél még nem teljesült. A két új felirathely nem jelent két új bizonyított
földrészletet, B besorolást vagy mai kataszteri létezési bizonyítékot.

| Telek | Tervlapi hely és képi bizonyíték | Övezeti eredmény | Minősítés | Fennmaradó akadály |
|---|---|---|---|---|
| Miskolc 4755/11 | [20-4 szelvény, 31. PDF-oldal](miskolc-plan-context.png); zárt tervlapi telek igazolt | Gipe-60.63.5 | B | Négy nyitott övezeti csatlakozás és eltérő útkitöltés; teljes övezeti fedés nincs igazolva. |
| Tiszaújváros 2200/8 | [35. PDF-oldal](tiszaújváros-2200-8.png); pontos felirat | Gip/3 | B | Teljes, igazolt telekpoligon és övezeti fedés hiányzik. |
| Komádi 1558 | [1. PDF-oldal](komádi-1558.png); pontos felirat | Lke/1.2 | B | Teljes telekpoligon és a teljes telek korlátozásvizsgálata hiányzik. |
| Budapest XII. 8448/46 | [Déli KÉSZ, 8. PDF-oldal](budapest-xii.-kerület-8448-46.png); két külön felirat | Nincs kiválasztott övezet; Lke-2/D-2, Lke-2/D-1 és Lk-2/D-6 környezeti feliratok | C | A feliratok közös telekhez rendelése, az északi/déli KÉSZ átfedése és a teljes övezeti kapcsolat nem igazolt. |
| Gersekarát 034/15 | [Hatályos rendelethez kapcsolt közigazgatási terv, 1. PDF-oldal](gersekarát-034-15.png); pontos felirat | Kb-Nk: kiolvasott jelölt | C | Csak 6/9 helyi kapcsolat; mai kataszteri állapot és teljes telek-/övezeti fedés nem igazolt. |

A képek az eredeti hivatalos PDF-ből készültek, a saját jelmagyarázat
kivágásaival. A cián jelölés a felirathelyet, Miskolcnál az igazolt tervlapi
telket mutatja. Az alkalmazás magyarázó színei nem hivatalos tervjelölések.
B: előzetes képi kapcsolat, feltételes előírásokkal. C: a besorolás nem
azonosítható, akkor is, ha egy térképi kód vagy HRSZ-felirat kiolvasható.
A kategóriák nem kalibrált valószínűségek.

## Ellenőrzött javítások

A kisméretű, szürke CAD-feliratokat az eredeti betűcsoport-index nem mindig
olvasta ki. Az új, átfedő térképlapka-OCR ettől függetlenül keres az egész
forrásban; forrás- és indexelőkód-lenyomathoz kötött, folytatható, más
HRSZ-ekhez is újrahasználható. A jelölt eredeti kivágatát 16×, 24× és 32×
léptékben ellenőrzi. A vonalakkal keresztezett feliratok külön ingyenes OCR-
szegmentálást kapnak. Lapkaeredetű helyet csak három stabil betűdobozból fogad
el; a helyes szöveg egy pontatlan térképi dobozban önmagában nem helybizonyíték.

Gersekarát pontos feliratát a 24× olvasat végén álló pont miatt korábban
elutasította. Csak ezt a záró írásjelet engedi elhagyni; a vezető nulla,
a belső számok és utótagok változatlanok. A közeli ismétlődő feliratokat külön
keresi és újraolvassa, majd ugyanazon lapon nem vonja össze telekgeometria
nélkül. A nagy CAD-övezeti betűket két nagyításban, kisebb kivágatokból is
vizsgálja. A tisztán számszerű építési paraméter nem válik övezeti kóddá.
A kiegészítő kódszintaxist a tényleges hatályos jogszabályból vezeti le;
szavak belsejéből kivágott részleges kódot nem fogad el.

Az eltérő elrendezésű saját jelmagyarázatnál legalább három külön feliratsor
alapján találja meg a távoli, keretezett mintaoszlopot. Az apró pontvonásokat
nem azonosítja az ugyanilyen színű folytonos szintvonalakkal. A meglevő,
helyesen olvasott vektormintákat megőrzi. A kis felbontású címkeresés nem
cserélheti le az első forráslap részletes jelmagyarázatát egy másik lap
részleges jelmagyarázatára: ezt a Komádi élő próbán feltárt regressziót is
javította. A helyi korlátozáskeresés csak a releváns kivágat natív rajzait
értékeli; az eredeti szakaszokat nem vágja le és nem zárja a kép szélével.

Mindhárom B kontrollt az eredeti forrásból újra megvizsgálta: Miskolc 5/9,
Tiszaújváros és Komádi 9/9 helyi kapcsolat. Ezek nem a teljes telek fedési
arányai. Gersekarát 6/9 eredményénél a küszöböt nem csökkentette. Az esetleges
többövezetes érintettség és a helyi korlátozások hiányának bizonyítása egyik
B/C vizsgálatnál sem teljes. A megoldásban nincs HRSZ-hez beégetett övezet.
Független, koordinátákkal igazolt utcahálózati/épületalaprajzi térképillesztés
még nem működik; országos kataszteri ellátottságot az öt próba nem bizonyít.

## Budapest XII. kerület 8448/46

Mindkét rendelet 2026.06.08. időállapotú hivatalos tervét teljes indexeléssel
vizsgálta. A [déli, 36/2021. KÉSZ](https://njt.jog.gov.hu/jogszabaly/2021-36-SP-5Y261)
[tervének](https://njt.jog.gov.hu/document/c3/c3f0LL_EJR_99708274-20250806_D-Hegyvid_k_K_SZ_1_mell_klet.pdf)
8. PDF-oldalán két külön 8448/46 felirat látható a Thomán István utca és a
Sólyom utca környezetében. Az [északi, 26/2020. KÉSZ](https://njt.jog.gov.hu/jogszabaly/2020-26-SP-5Y261)
[peremlapjain](https://njt.jog.gov.hu/document/2a/2afbLL_EJR_99704828-20250806__-Hegyvid_k_K_SZ_1_mell_klet.pdf)
is megtalálhatók HRSZ-feliratok a 63–64. PDF-oldalon. A háttértérképi felirat
nem bizonyítja az adott rendelet területi hatályát. A program ezért az
északi/déli forrásválasztás bizonytalanságát külön megőrzi.

A déli terv saját telekhatármintájával kiválasztott natív szakaszokból végzett
helyi poligonizálás nem adott mindkét feliratot tartalmazó zárt telekterületet.
A 0,00001–0,001 PDF-pontos kerekítési próbák sem változtatták meg ezt. Apró
betűalakokból keletkező zárt felületek nem telekpoligonok. A szomszédos
Lke-2/D-2 és Lke-2/D-1 felirat közelségéből nem választ övezetet.

Két további valódi kontroll, 8448/47 és 8448/56, szomszédos lapokon is
visszaolvasható. Az összevetés csak három közös, eredeti kivágatokból
ellenőrzött támpontot adott, az előírt legalább öt helyett: a próbák nem
igazolnak tervlapillesztést vagy övezetet. A receiptek a JSON
`additional_localization_checks` mezőjében vannak; a forrásellenőrzés külön
újraszámítja őket. Egy szintetikus pozitív illesztési teszt nem valós telekpróba.

Az engedélyezett OÉNY WMS-próba korábbi HTML-hibaválaszát a
[meglévő jegyzőkönyv](budapest-xii-wms-receipt.json) megőrzi. Nem ismételtük meg,
nem kerültük meg a korlátozást és nem küldtünk koordinátákat új célhelyre.

## Gersekarát 034/15

A [hatályos rendelet](https://njt.jog.gov.hu/jogszabaly/2007-2-SP-5Y3101)
2019.06.01. időállapotához kapcsolt [hivatalos közigazgatási terv](https://njt.jog.gov.hu/document/ff/ffecLL_EJR_55522770-2._mell_klet_H_SZ.pdf)
1. PDF-oldalán a 034/15 felirat három felbontásban visszaolvasható.
Ez az ábrázolt tervállapotot igazolja. Az OÉNY pontos találatának hiánya és a
korábbi önkormányzati tanulmány nem bizonyítja a telek mai megszűnését,
átnevezését vagy jelenlegi kataszteri alakját.

A helyi tervkivágaton a Kb-Nk kód két nagyításban kiolvasható; határkapcsolata
6/9, ezért **C jelölt**. Feltételesen kapcsolódik a teljes
[21/A. §](https://njt.jog.gov.hu/jogszabaly/2007-2-SP-5Y3101#SZ21A@BE0):
a naperőmű technológiai építményei és műtárgyai; az épületek legnagyobb
építménymagassága 10,0 m, a nem épület technológiai műtárgyak kivételével;
a beépíthető telekrészen kívül gyepszintű növényzetet kell fenntartani,
fás növény nem tartható meg és új nem ültethető. Ezek **forrásszabályok,
nem a 034/15 telken igazolt építési jogosultságok**. A teljes a) és b)
rendelkezés, hivatkozás, időállapot és szöveglenyomat a JSON-ban megmarad.

## Komádi 1558 – előírások és megmaradt hiány

Az Lke/1.2 kód saját kör alakú kódmezőből olvasható, 9/9 helyi kapcsolattal.
A [hatályos rendelet](https://njt.jog.gov.hu/jogszabaly/2007-1-SP-5Y1608)
2025.12.22. állapotához kapcsolt
[egységes HÉSZ-PDF](https://njt.jog.gov.hu/document/3e/3ef5LL_EJR_109881481-Helyi__p_t_si_Szabalyzat_EGYS_GES_202512_-_pirossal_a_v_ltoz_s_F_zesiAttila.pdf)
teljes szakaszait a forrásleltár betölti; a 15. § összefoglaló táblázata a
14. PDF-oldaltól szerepel. A táblázat kör alakú övezeti jelképét még nem
oldja fel ellenőrzött, strukturált Lke/1.2 paramétersorként. Emiatt nincs
ilyen igazolt paraméterlista. A teljes forrásszakaszok megtartása nem
helyettesíti a táblázat és a teljes telek kapcsolatának igazolását.

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
| Gersekarát | [hivatalos közigazgatási terv](https://njt.jog.gov.hu/document/ff/ffecLL_EJR_55522770-2._mell_klet_H_SZ.pdf), 1. PDF-oldal | két felbontásban egyező felirat; a távoli, keretezett mintaoszlopból kiolvasott apró piros pontvonások |
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

## Megismételhetőség és eredményellenőrzés

**171/171 offline automatikus teszt sikeres**, a 36 eredeti teszttel.
**260/260 forrásalapú ellenőrzés sikeres**: eredeti PDF-bájtok,
friss NJT-szöveg, saját jelmagyarázat gyorsítótár nélküli újraolvasása,
háromfelbontásos feliratok, az alternatív KÉSZ-ek, az öt képi bizonyíték,
a két további HRSZ-kontroll, feltételes jogszabályi szakaszok és a korábbi
valódi helyi modellreceiptek ellenőrzése. A tesztszám nem besorolási pontosság.

```sh
python -m unittest discover
python reference_checks.py --resume-attempts 3 --visual-images validation --visual-image validation/miskolc-plan-context.png --output validation/reference-results.json
python validation/check_sources.py validation/reference-results.json --history-report validation/miskolc-legend-history.json --local-vision-report validation/local-vision-results.json --output validation/source-check-results.json
```

A két kiegészítő kontroll külön kísérleti receipt, nem a fenti öttelekes
parancs automatikus kimenete. A JSON-ban a forrás URL-je, eredeti lenyomata,
futókód-lenyomatok, jogszabályi kiadás és a kivágási koordináták szerepelnek.
A [forrásellenőrzés](source-check-results.json) a teljes jelentés lenyomatát
is rögzíti. A GitHub Actions a PR ágának aktuális commitján futtatja az
offline regressziót; a PR nincs beolvasztva.

A következő bizonyításhoz aktuális, jogszerűen elérhető kataszteri telekhatár,
a terv saját jeleivel összevethető övezeti topológia és az átfedő tervek
hivatalos területi határa szükséges. Miskolcnál a nyitott csatlakozások és
útjelváltozat hivatalos feloldása, Gersekarát esetében a mai kataszteri állapot
ellenőrzése külön akadály. A teljes korlátozáslista és az országos/átmeneti
jogszabályi feltételek telekspecifikus alkalmazhatósága továbbra is hiányzik.
Országos teljes automatizálást vagy A eredményt nem állítunk.

Ebben a munkamenetben nincs új nagy modell, fizetős API-hívás vagy új pénzügyi
kötelezettség. A meglévő helyi modelleket nem futtattuk újra: az alábbi
összevetés a korábbi tényleges, ingyenes próbák megőrzött jegyzőkönyve.

## Korábbi ingyenes multimodális modellek valódi összevetése

A korábbi próba eredeti képkivágási geometriáját a modelljegyzőkönyv
`image_input_manifests` mezője őrzi, az eredeti Git-commit hivatkozásával.
A mai jelmagyarázat-parser új nagyított jelmintákat is felismer: ezekkel új
modellpróba nem futott. Az eredeti térképrészlet és teljes jelmagyarázat
megmaradt; a korábbi modellválaszok és képlenyomatok változatlanok.

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
referencia. A korábbi modellpróba az övezeti eredmények számát nem növelte: 3/5 B,
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
