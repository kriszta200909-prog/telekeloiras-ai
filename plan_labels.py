"""Recover outlined CAD labels without municipality-specific coordinates.

Recognising a parcel number is a map label candidate, not parcel geometry or
zoning proof. The module never assigns a zone from a nearby inscription.
"""
import math
import io
import re
import time
from collections import defaultdict

import fitz
import numpy as np
from PIL import Image, ImageOps


def exact_hrsz_token(value, target):
    """Preserve zeros, suffixes and separators; strip only enclosing punctuation."""
    normalize=lambda s: re.sub(r'\s*([/-])\s*',r'\1',str(s or '').strip()).strip('(),;:[]{}')
    wanted=normalize(target)
    return bool(wanted and normalize(value)==wanted)


def native_hrsz_hits(doc, target):
    """Substring search must not accept 1558 inside 15580 or 1558/1."""
    target=re.sub(r'\s+','',str(target or ''))
    if not target:return []
    expression=''.join(r'\s*'+re.escape(c)+r'\s*' if c in '/-' else re.escape(c) for c in target)
    pattern=re.compile(r'(?<![\w/.-])'+expression+r'(?!\s*[/.-]|\w)')
    hits=[]
    for number,page in enumerate(doc):
        raw=page.get_text('rawdict',flags=fitz.TEXTFLAGS_RAWDICT & ~fitz.TEXT_PRESERVE_IMAGES)
        for block in raw.get('blocks',[]):
            if block.get('type')!=0:continue
            for line in block.get('lines',[]):
                chars=[c for span in line.get('spans',[]) for c in span.get('chars',[])]
                text=''.join(c['c'] for c in chars)
                for match in pattern.finditer(text):
                    rect=fitz.Rect(chars[match.start()]['bbox'])
                    for c in chars[match.start()+1:match.end()]:rect|=fitz.Rect(c['bbox'])
                    hits.append({'page_number':number,'pdf_rect':tuple(rect),
                                 'method':'pontos natív HRSZ-felirat','label_only':True})
    return hits


def _label_image(display_list, group, scale):
    rect=fitz.Rect(group['rect'])+(-.35,-.35,.35,.35)
    pix=display_list.get_pixmap(matrix=fitz.Matrix(scale,scale),clip=rect,alpha=False)
    image=Image.frombytes('RGB',(pix.width,pix.height),pix.samples)
    return image.rotate(group['angle'],expand=True,fillcolor='white')


def _ocr_words(image, tessdata):
    data=io.BytesIO();image.save(data,format='PNG')
    pix=fitz.Pixmap(data.getvalue());pix.set_dpi(150,150)
    with fitz.open(stream=pix.pdfocr_tobytes(language='eng',tessdata=tessdata),filetype='pdf') as doc:
        return [(w[4],(w[0]+w[2])/2*150/72,(w[1]+w[3])/2*150/72)
                for w in doc[0].get_text('words')]


def outlined_label_index(doc, tessdata, max_seconds=180, on_progress=None):
    """Bounded montage OCR supplies reusable number-label candidates.

    Completeness means all eligible path groups were processed, not that every
    printed label was recognised. No municipal name or HRSZ is embedded.
    """
    started=time.monotonic();labels=[];scanned=0;total=0;pages=0
    for number,page in enumerate(doc):
        groups=outlined_label_groups(page)
        # Rotated long numbers remain eligible; orientation comes from paths.
        groups=[g for g in groups if max(fitz.Rect(g['rect']).width,fitz.Rect(g['rect']).height)<30]
        total+=len(groups)
        display_list=page.get_displaylist()
        try:
            for offset in range(0,len(groups),64):
                if time.monotonic()-started>max_seconds:
                    return {'labels':labels,'complete':False,'scanned':scanned,'candidates':total,'pages':pages}
                batch=groups[offset:offset+64]
                cell_w,cell_h=200,80;columns=4
                montage=Image.new('RGB',(cell_w*columns,cell_h*math.ceil(len(batch)/columns)),'white')
                for i,group in enumerate(batch):
                    label=_label_image(display_list,group,16)
                    if label.width>cell_w-16 or label.height>cell_h-16:
                        label.thumbnail((cell_w-16,cell_h-16),Image.Resampling.LANCZOS)
                    montage.paste(label,((i%columns)*cell_w+(cell_w-label.width)//2,
                                         (i//columns)*cell_h+(cell_h-label.height)//2))
                words=_ocr_words(montage,tessdata)
                matched=defaultdict(set)
                for word,x,y in words:
                    i=int(y//cell_h)*columns+int(x//cell_w)
                    token=word.strip('(),;:[]{}')
                    if 0<=i<len(batch) and re.fullmatch(r'\d+(?:[/-][A-Za-z0-9]+)*',token):
                        matched[i].add(token)
                for i,tokens in matched.items():
                    group=batch[i]
                    for token in tokens:
                        labels.append({'page_number':number,'pdf_rect':group['rect'],
                                       'angle':group['angle'],'text':token,'label_only':True})
                scanned+=len(batch)
                if on_progress:on_progress(number+1,len(doc),scanned,len(labels))
            pages+=1
        finally:
            del display_list
            fitz.TOOLS.store_shrink(100)
    return {'labels':labels,'complete':True,'scanned':scanned,'candidates':total,'pages':pages}


def verify_outlined_hrsz(doc, index, target, tessdata):
    """Re-read source crops at two larger resolutions; never infer a zone."""
    hits=[]
    candidates=[label for label in index.get('labels',[]) if exact_hrsz_token(label['text'],target)]
    for number in sorted({label['page_number'] for label in candidates}):
        display_list=doc[number].get_displaylist()
        try:
            for candidate in candidates:
                if candidate['page_number']!=number:continue
                group={'rect':candidate['pdf_rect'],'angle':candidate['angle']};confirmed=[]
                for scale in (24,32):
                    label=ImageOps.expand(_label_image(display_list,group,scale),border=30,fill='white')
                    confirmed.append(any(exact_hrsz_token(word,target) for word,_,_ in _ocr_words(label,tessdata)))
                    if not confirmed[-1]:break
                if confirmed==[True,True]:
                    hits.append({**candidate,'method':'rajzi HRSZ-felirat, három felbontásban egyező felismerés'})
        finally:
            del display_list
            fitz.TOOLS.store_shrink(100)
    return {'hits':hits,'complete':index.get('complete',False),
            'scanned':index.get('scanned',0),'candidates':index.get('candidates',0),'pages':index.get('pages',0)}


def outlined_hrsz_hits(doc,target,tessdata,max_seconds=180,on_progress=None):
    index=outlined_label_index(doc,tessdata,max_seconds,on_progress)
    return verify_outlined_hrsz(doc,index,target,tessdata)


def outlined_label_groups(page, padding=.2):
    """Group nearby compact neutral filled paths using page geometry only."""
    rects=[]
    matrix=page.rotation_matrix
    def collect(drawing):
        fill=drawing.get('fill')
        if not fill or max(fill)-min(fill)>.025 or max(fill)>.85:
            return
        rect=fitz.Rect(drawing['rect'])*matrix
        if .04<rect.width<7 and .04<rect.height<7:
            rects.append(tuple(rect))
    page.get_cdrawings(callback=collect)
    parents=list(range(len(rects)))
    cells=defaultdict(list)
    def root(i):
        while parents[i]!=i:
            parents[i]=parents[parents[i]];i=parents[i]
        return i
    for i,(x0,y0,x1,y1) in enumerate(rects):
        neighbors=set()
        xs=range(math.floor((x0-padding)/4),math.floor((x1+padding)/4)+1)
        ys=range(math.floor((y0-padding)/4),math.floor((y1+padding)/4)+1)
        for x in xs:
            for y in ys: neighbors.update(cells[(x,y)])
        for j in neighbors:
            a,b,c,d=rects[j]
            if x0-padding<=c+padding and x1+padding>=a-padding and y0-padding<=d+padding and y1+padding>=b-padding:
                ri,rj=root(i),root(j)
                if ri!=rj: parents[rj]=ri
        for x in xs:
            for y in ys: cells[(x,y)].append(i)
    groups=defaultdict(list)
    for i,rect in enumerate(rects): groups[root(i)].append(rect)
    results=[]
    for pieces in groups.values():
        if len(pieces)<3: continue
        points=np.array([((r[0]+r[2])/2,(r[1]+r[3])/2) for r in pieces])
        if len(points)>1:
            _,vectors=np.linalg.eigh(np.cov(points.T))
            axis=vectors[:,-1]
            if axis[0]<0:axis=-axis
            angle=math.degrees(math.atan2(axis[1],axis[0]))
        else:angle=0
        bbox=fitz.Rect(min(r[0] for r in pieces),min(r[1] for r in pieces),
                       max(r[2] for r in pieces),max(r[3] for r in pieces))
        if not (2<max(bbox.width,bbox.height)<45 and min(bbox.width,bbox.height)>.5): continue
        results.append({'rect':tuple(bbox),'angle':angle,'pieces':len(pieces)})
    return results
