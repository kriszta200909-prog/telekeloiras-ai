
import re, collections
import fitz
import pandas as pd
import streamlit as st
from shapely.geometry import LineString, Point
from shapely.ops import unary_union, polygonize

ZONE_RE=re.compile(
 r"\b(?:Lk|Lke|Ln|Vt|Vi|Gksz|Gip|Ge|Gá|K|Kb|KÖu|KÖk|Zkp|Zkk|Z|Ev|Ek|Má|Mk|V|Ve|Lf|Üü)"
 r"(?:[-/][A-Za-zÁÉÍÓÖŐÚÜŰáéíóöőúüű0-9]+){0,5}\b")

def ncode(s):
    s=(s or "").strip().replace("–","-").replace("—","-")
    return re.sub(r"\s*([/-])\s*",r"\1",s)

def hits(page,hrsz):
    out=[]
    for v in {hrsz,f"({hrsz})",hrsz.replace("/"," / "),hrsz.replace("/","/ ")}:
        out += list(page.search_for(v))
    return out

def legal_codes(data):
    doc=fitz.open(stream=data,filetype="pdf"); codes=set()
    for p in doc:
        for m in ZONE_RE.finditer(p.get_text()):
            codes.add(ncode(m.group(0)))
    return sorted(codes)

def style_key(path):
    c=path.get("color")
    if isinstance(c,(tuple,list)): c=tuple(round(float(x),3) for x in c)
    return (round(float(path.get("width") or 0),2),str(path.get("dashes") or ""),
            c,str(path.get("layer") or ""),str(path.get("type") or ""))

def groups(page):
    g=collections.defaultdict(list)
    for path in page.get_drawings():
        k=style_key(path)
        for it in path.get("items",[]):
            if it[0]=="l":
                a,b=it[1],it[2]; g[k].append(LineString([(a.x,a.y),(b.x,b.y)]))
            elif it[0]=="re":
                r=it[1]; pts=[(r.x0,r.y0),(r.x1,r.y0),(r.x1,r.y1),(r.x0,r.y1),(r.x0,r.y0)]
                g[k]+=[LineString([a,b]) for a,b in zip(pts[:-1],pts[1:])]
    return g

def polys(lines,grid=.75):
    if len(lines)<3:return []
    try:return [p for p in polygonize(unary_union(lines,grid_size=grid)) if p.area>=20]
    except:return []

def labels(page):
    out=[]
    for w in page.get_text("words"):
        c=ncode(w[4].strip("()[]{}.,;:"))
        if ZONE_RE.fullmatch(c):
            out.append({"code":c,"pt":Point((w[0]+w[2])/2,(w[1]+w[3])/2)})
    return out

def geom(page,hp,grid=.75):
    labs=labels(page); parcel=[]; zones=[]
    for k,lines in groups(page).items():
        pp=polys(lines,grid); contain=[p for p in pp if p.covers(hp)]
        codes=sorted(set(z["code"] for p in contain for z in labs if p.covers(z["pt"])))
        width,dashes,color,layer,typ=k; ps=zs=0
        if width and width<=.5: ps+=20
        if width and width>=.5: zs+=15
        if dashes not in ("","None","[] 0"): zs+=25
        lo=layer.lower()
        if "telek" in lo or "parcel" in lo: ps+=55
        if "övezet" in lo or "ovezet" in lo or "zone" in lo: zs+=65
        if contain: ps+=15; zs+=15
        if codes: zs+=30
        if ps>=35: parcel+=contain
        if zs>=45: zones+=contain
    parcel=sorted(parcel,key=lambda p:p.area)
    if not parcel:return []
    p=parcel[0]; best={}
    for zp in zones:
        inter=p.intersection(zp)
        if inter.is_empty or inter.area<=0:continue
        pct=100*inter.area/p.area
        for z in labs:
            if zp.covers(z["pt"]): best[z["code"]]=max(best.get(z["code"],0),pct)
    return sorted([{"code":k,"pct":v} for k,v in best.items()],key=lambda x:x["pct"],reverse=True)

def verdict(g,codes):
    if not g:return "","NEM MEGHATÁROZHATÓ","A tervből nem rekonstruálható megbízható övezeti geometria."
    top=g[0]; exact=top["code"] in codes
    competitors=[x for x in g[1:] if x["pct"]>=5 and x["code"]!=top["code"]]
    if top["pct"]>=95 and exact and not competitors:
        return top["code"],"ERŐSEN IGAZOLT JELÖLT","≥95% geometriai metszés és pontos HÉSZ-kódegyezés."
    if exact and top["pct"]>=70:return top["code"],"ELLENŐRIZENDŐ","A HÉSZ-kód egyezik, de a geometria nem teljesen egyértelmű."
    if top["pct"]>=95:return top["code"],"ELLENŐRIZENDŐ","A geometria erős, de nincs pontos HÉSZ-kódegyezés."
    return top["code"],"BIZONYTALAN","A két bizonyítási ág együtt nem elég erős."

def crop(page,r,m=600):
    cx=(r.x0+r.x1)/2; cy=(r.y0+r.y1)/2
    clip=fitz.Rect(max(0,cx-m),max(0,cy-m),min(page.rect.width,cx+m),min(page.rect.height,cy+m))
    return page.get_pixmap(matrix=fitz.Matrix(1.7,1.7),clip=clip,alpha=False).tobytes("png")

st.set_page_config(page_title="TelekElőírás AI", page_icon="🏗️", layout="wide")
st.title("TelekElőírás AI")
st.caption("Privát tesztverzió • AI-előszűrés, szakmai visszaellenőrzéssel")

with st.sidebar:
    st.header("Tesztforrások")
    st.info("Ebben az első webes tesztben töltsd fel a hatályos SZT-t és HÉSZ/TÉSZ-t. A következő lépés ezek automatikus hivatalos forrásból történő beszerzése.")
    plan=st.file_uploader("Szabályozási terv (PDF)",type=["pdf"])
    hesz=st.file_uploader("HÉSZ/TÉSZ vagy övezeti melléklet (PDF)",type=["pdf"])
    grid=st.slider("Geometriai tolerancia",.1,3.0,.75,.05)

c1,c2=st.columns(2)
town=c1.text_input("Település","Tiszaújváros")
hrsz=c2.text_input("Helyrajzi szám","2200/8")

if st.button("Vizsgálat indítása",type="primary",use_container_width=True):
    if not plan or not hesz:
        st.error("Az első privát webes teszthez töltsd fel az SZT és HÉSZ/TÉSZ PDF-et a bal oldalon.")
        st.stop()
    pbytes=plan.getvalue(); hbytes=hesz.getvalue()
    codes=legal_codes(hbytes)
    doc=fitz.open(stream=pbytes,filetype="pdf")
    found=[]
    for pno,p in enumerate(doc):
        for r in hits(p,re.sub(r"\s+","",hrsz)):
            hp=Point((r.x0+r.x1)/2,(r.y0+r.y1)/2)
            g=geom(p,hp,grid)
            code,status,why=verdict(g,codes)
            found.append((pno,r,g,code,status,why))
    if not found:
        st.warning("A HRSZ nem található gépi szövegként a tervben. Ez a terv vision/scan feldolgozást igényel.")
    else:
        pno,r,g,code,status,why=found[0]
        a,b,c=st.columns(3)
        a.metric("Övezet-jelölt",code or "—")
        b.metric("Státusz",status)
        c.metric("Legjobb metszés",f"{g[0]['pct']:.1f}%" if g else "—")
        st.write(why)
        st.subheader("Térképi bizonyíték")
        st.image(crop(doc[pno],r),use_container_width=True)
        if g:
            df=pd.DataFrame(g)
            df["HÉSZ exact match"]=df["code"].isin(codes)
            st.dataframe(df,use_container_width=True)
        st.checkbox("Szakmailag visszaellenőriztem az eredményt")
        st.caption(f"Felhasznált tervoldal: {pno+1}. oldal • HÉSZ-ben felismert övezeti kódok: {len(codes)}")

st.divider()
st.caption("Tesztalkalmazás. Az eredmény nem helyettesíti a hatályos jogszabály és tervdokumentáció szakmai ellenőrzését.")
