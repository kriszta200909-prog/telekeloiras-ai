# TelekElőírás AI – privát webes teszt

Streamlit Community Cloud-ra feltölthető változat.

## Aktuális verzió: v6.1

A v4.4 célja, hogy a vizsgálat végén közvetlenül erre a kérdésre adjon forrásolt választ:

> **Mit lehet és mit nem lehet ezen a konkrét telken csinálni, és ezt melyik hatályos forrás mondja?**

A program:
- az NJT hatályos rendeletszövegét és elérhető mellékleteit használja;
- a helyrajzi számot a szabályozási tervben keresi;
- az övezeti besorolást csak térbeli igazolás után kezeli telekspecifikus jogi alapként;
- elkülöníti a megengedő, tiltó és feltételt/korlátot tartalmazó előírásokat;
- megjeleníti a kinyerhető beépítési paramétereket és azok forrását;
- nem állít telekspecifikus következtetést pusztán azért, mert egy korlátozástípus szerepel a TÉSZ-ben;
- nem talál ki hiányzó adatot vagy nem dokumentált E-közmű API-végpontot.

## Fájlok
- `app.py`
- `requirements.txt`
- `packages.txt`

A Streamlit indítógomb felirata verziófüggetlen: **Telekvizsgálat indítása**.
