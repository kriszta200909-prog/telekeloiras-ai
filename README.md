# TelekElőírás AI – privát webes teszt

Streamlit Community Cloud-ra feltölthető változat.

## Aktuális verzió: v6.2

A v6.2 fő javítása az NJT-forráskezelés:

- a program elsődlegesen a `https://njt.jog.gov.hu/jogszabaly/...` hivatalos jogszabályoldalakat keresi és validálja;
- Tiszaújváros HÉSZ-ének validált kanonikus forrása: `https://njt.jog.gov.hu/jogszabaly/2018-11-SP-5Y1228`;
- a Tiszaújváros-forrásnál nem generál `or.njt.hu` URL-t;
- az ingyenes webes felderítő a találatot településnév, „építési szabályzat”, „szabályozási terv” és melléklet/övezeti tartalom alapján ellenőrzi;
- történeti NJT URL-változat esetén a kanonikus jogszabályoldalt használja;
- a kézi szabályozási terv PDF továbbra is tartalék/ellenőrzési lehetőség.

## Cél

A vizsgálat végén közvetlenül erre a kérdésre adjon forrásolt választ:

> **Mit lehet és mit nem lehet ezen a konkrét telken csinálni, és ezt melyik hatályos forrás mondja?**

## GitHub / Streamlit

A GitHub repóban az `app.py` legyen a v6.2 fájl, és a `README.md` is ezt a verziót mutassa. A Streamlit Community Cloud az `app.py`-t futtassa.
