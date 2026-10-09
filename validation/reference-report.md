# Öt mintatelek hivatalos forrásellenőrzése

Futás kezdete: 2026-10-09 18:46 CEST.

A végső forráslekérési és GeoPDF-próba eredménye a `reference-results.json` fájlban van. A külön, teljes görbefelirat-keresési próba a `reference-outlined-results.json` fájlban található. Mindkettő élő hivatalos forrásokkal futott, forráslenyomatokat és futáskód-lenyomatokat rögzít.

Az offline regresszió 60/60 tesztje, a teljes Streamlit vizsgálati útvonal és a JSON-letöltés tesztje, valamint a Python-szintaktikai ellenőrzés sikeres.

| Telek | Pontos hivatalos HRSZ | Telekgeometria | Övezet teljes poligonfedéssel | Teljes telekspecifikus hatályos előíráskör |
|---|---|---|---|---|
| Tiszaújváros 2200/8 | igen | nem igazolt | nem igazolt | nem igazolt |
| Budapest XII. kerület 8448/46 | igen | nem igazolt | nem igazolt | nem igazolt |
| Komádi 1558 | igen | nem igazolt | nem igazolt | nem igazolt |
| Gersekarát 034/15 | nem igazolt | nem igazolt | nem igazolt | nem igazolt |
| Miskolc 4755/11 | igen | igen, tervlapi határ* | nem igazolt | nem igazolt |

*A miskolci zárt tervlapi telekhatár a PDF 31. oldalának saját GEO-koordinátáiból és a határon belüli pontos HRSZ-ből ellenőrizhető. A becsült rajzi bizonytalanság ±1,07 m; ez nem hiteles földmérési telekhatár.

## Telekenkénti részletek

### Tiszaújváros 2200/8

- HRSZ: pontos hivatalos HRSZ-találat.
- Geometria: csak megjelenítési poligon / nincs igazolt telekhatár.
- Övezet: jelölt: Gip/3; poligonfedés nem igazolt.
- Helyi NJT-forrás ellenőrizve: True; időállapot: 2024.10.10.; kinyert témaköri sorok: 172.
- A rendelet és a terv betöltése nem bizonyítja az összes előírás telekre alkalmazhatóságát.
- Tervfeldolgozás: Pontos HRSZ és övezeti körjel két felbontásban; vektorillesztés, teljes szelvényfedés és belső övezethatár-vizsgálat ellenőrizve.
- [Hivatalos HRSZ-lekérdezés](https://www.oeny.hu/hk-api/parcels/search?kshCode=28352&lotNumber=2200%2F8)
- [Hivatalos megjelenítési körvonal](https://www.oeny.hu/hk-api/parcels/bounding-box?id=5104713)
- [NJT-rendelet](https://njt.jog.gov.hu/jogszabaly/2018-11-SP-5Y1228)
- [Letöltött hivatalos szabályozási terv](https://njt.jog.gov.hu/document/d9/d95fLL_EJR_81697536-rendelet_mell_klet-1.pdf)
- PDF SHA-256: `dd81c298d2b12e85d21ea258f3dabae97525d72ead32134d1b8c363aa452d9ef`.

### Budapest XII. kerület 8448/46

- HRSZ: pontos hivatalos HRSZ-találat.
- Geometria: csak megjelenítési poligon / nincs igazolt telekhatár.
- Övezet: nincs igazolt övezet.
- Helyi NJT-forrás ellenőrizve: True; időállapot: 2026.06.08.; kinyert témaköri sorok: 266.
- A rendelet és a terv betöltése nem bizonyítja az összes előírás telekre alkalmazhatóságát.
- MINERVA: MINERVA térbeli lekérdezés: HTTPError: HTTP Error 503: Service Unavailable
- Tervfeldolgozás: Nincs együtt igazolt GEO-illesztés, zárt telekhatár és pontos HRSZ.
- [Hivatalos HRSZ-lekérdezés](https://www.oeny.hu/hk-api/parcels/search?kshCode=24697&lotNumber=8448%2F46)
- [Hivatalos megjelenítési körvonal](https://www.oeny.hu/hk-api/parcels/bounding-box?id=2637534)
- [NJT-rendelet](https://njt.jog.gov.hu/jogszabaly/2021-36-SP-5Y261)
- [Letöltött hivatalos szabályozási terv](https://njt.jog.gov.hu/document/c3/c3f0LL_EJR_99708274-20250806_D-Hegyvid_k_K_SZ_1_mell_klet.pdf)
- PDF SHA-256: `98b958834b26759e93367f3a5f025de7264e5c7fdbb9fcd4fded0627bb52b84f`.

### Komádi 1558

- HRSZ: pontos hivatalos HRSZ-találat.
- Geometria: csak megjelenítési poligon / nincs igazolt telekhatár.
- Övezet: nincs igazolt övezet.
- Helyi NJT-forrás ellenőrizve: True; időállapot: 2025.12.22.; kinyert témaköri sorok: 58.
- A rendelet és a terv betöltése nem bizonyítja az összes előírás telekre alkalmazhatóságát.
- Tervfeldolgozás: Nincs együtt igazolt GEO-illesztés, zárt telekhatár és pontos HRSZ.
- Külön feliratellenőrzés: [{'page': 1, 'method': 'rajzi HRSZ-felirat, három felbontásban egyező felismerés'}]. Ez a feliratot, nem a telekhatárt igazolja.
- [Hivatalos HRSZ-lekérdezés](https://www.oeny.hu/hk-api/parcels/search?kshCode=02167&lotNumber=1558)
- [Hivatalos megjelenítési körvonal](https://www.oeny.hu/hk-api/parcels/bounding-box?id=6220536)
- [NJT-rendelet](https://njt.jog.gov.hu/jogszabaly/2007-1-SP-5Y1608)
- [Letöltött hivatalos szabályozási terv](https://njt.jog.gov.hu/document/29/29e9LL_EJR_109881483-tervlap_T_3_BELTERULETI_SZAB_2025_egyben.pdf)
- PDF SHA-256: `15422b7898f58ab3763cbf2289e9b81b1aa4c548ecb5c8721df1e51800c9a041`.

### Gersekarát 034/15

- HRSZ: nincs pontos hivatalos HRSZ-találat.
- Geometria: csak megjelenítési poligon / nincs igazolt telekhatár.
- Övezet: nincs igazolt övezet.
- Helyi NJT-forrás ellenőrizve: True; időállapot: 2019.06.01.; kinyert témaköri sorok: 14.
- A rendelet és a terv betöltése nem bizonyítja az összes előírás telekre alkalmazhatóságát.
- Lekérési/feldolgozási korlát: HRSZ/telek: A kereső nem adott pontosan egyező HRSZ-rekordot.
- Tervfeldolgozás: Nincs pontos HRSZ-hez kapcsolt megjelenítési poligon a tervlapkereséshez.
- [NJT-rendelet](https://njt.jog.gov.hu/jogszabaly/2007-2-SP-5Y3101)
- [Letöltött hivatalos szabályozási terv](https://njt.jog.gov.hu/document/0d/0dafLL_EJR_116753400-Gersekar_t-szt.pdf)
- PDF SHA-256: `be680e7b2cd26973a34be6ad3e1c9c9f9118421cf668ba991641c8ea7f64e567`.

### Miskolc 4755/11

- HRSZ: pontos hivatalos HRSZ-találat.
- Geometria: zárt tervlapi telekhatár; bizonytalanság: 1.0701384986729763 m.
- Övezet: jelölt: Gipe-60.63.5; poligonfedés nem igazolt.
- Helyi NJT-forrás ellenőrizve: True; időállapot: 2026.09.26.; kinyert témaköri sorok: 253.
- A rendelet és a terv betöltése nem bizonyítja az összes előírás telekre alkalmazhatóságát.
- Tervfeldolgozás: Pontos HRSZ és zárt tervlapi telekhatár a PDF saját koordinátáiból ellenőrizve. Az övezetfelirat még jelölt: teljes, feliratozott övezetpoligon nem igazolt.
- [Hivatalos HRSZ-lekérdezés](https://www.oeny.hu/hk-api/parcels/search?kshCode=30456&lotNumber=4755%2F11)
- [Hivatalos megjelenítési körvonal](https://www.oeny.hu/hk-api/parcels/bounding-box?id=5143390)
- [NJT-rendelet](https://njt.jog.gov.hu/jogszabaly/2022-38-SP-5Y1070)
- [Letöltött hivatalos szabályozási terv](https://njt.jog.gov.hu/document/b4/b45dLL_EJR_127797597-Belteruleti_szabalyozasi_tervlapok_modositasa.pdf)
- PDF SHA-256: `187187fd5b5b86ed34bb8e797bb2ed143c9a065f27187e12fb1d88d094c5e4bc`.

## Fennmaradó konkrét korlátok

- Az OÉNY bounding-box válaszában szereplő lekerekített körvonal megjelenítési geometria. Nem helyettesíti a tervből vagy önálló hivatalos határadatból igazolt telekpoligont.
- Tiszaújváros: a 2200/8 felirat és a Gip/3 körjel felismerhető; a korábbi adapter a megjelenítési körvonalból visszaszámolt geometriát használja. Teljes, forrásból származó telek- és övezetpoligon nélkül ez nem kap geometriai igazolást.
- Budapest XII.: a hivatalos terv betöltődött, de a MINERVA HTTP 503 választ adott. A PDF nem tartalmaz támogatott beágyazott GEO-adatot, ezért a zárt övezetkapcsolat nem igazolt.
- Komádi: az 1558 HRSZ a hivatalos keresőben és a terv első oldalán is ellenőrizhető. A terv nem tartalmaz támogatott GEO-adatot; a rajzi felirat nem pótolja az illesztést és az övezethatárt.
- Gersekarát: a 034/15-re nincs pontos hivatalos keresőtalálat. A támogatott feliratkeresés sem adott igazolt találatot; ebből nem következik a telek hiánya. A rendelet déli községrészre vonatkozó területi kizárását is külön ellenőrizni kell.
- Miskolc: a Gipe-60.63.5 felirat önállóan kiolvasható, de a teljes zárt övezetpoligon a támogatott poligonrétegekből nem állítható elő. A pontozott határt nem alakítjuk kitalált zárt poligonná.
- Az összes területi, helyi és országos előírás teljes alkalmazhatósága egyik telekre sem igazolt.

## Megismétlés

```sh
python -m unittest discover -v
python reference_checks.py --output work/reference-results.json
python reference_checks.py --outlined --output work/reference-outlined-results.json
```

A külső források későbbi elérhetősége és kiadása eltérhet. A JSON-eredmények a rögzített futás megfigyelései, nem újabb futás helyettesítői.
