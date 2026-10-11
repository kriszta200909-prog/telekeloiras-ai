"""Source-bound supplementary map OCR, independent of CAD glyph grouping.

The index is shared between parcel numbers. A recognised inscription locates
text only; it does not certify a cadastral polygon or assign a zoning code.
"""
import hashlib
import io
import json
import math
import re
import tempfile
import time
import shutil
import subprocess
from pathlib import Path

import fitz
from PIL import Image, ImageOps
from plan_labels import OCRWorker, _label_image, _ocr_words, exact_hrsz_token

IMPLEMENTATION_HASH=hashlib.sha256(Path(__file__).read_bytes()).hexdigest()


def exact_map_token(value, target):
    # A terminal full stop is OCR punctuation, never an internal parcel suffix.
    return exact_hrsz_token(str(value).rstrip('.'),target)


def sparse_words(image,tessdata):
    """Alternate free OCR segmentation for text crossed by map linework."""
    if not shutil.which('tesseract'):return []
    raw=io.BytesIO();image.save(raw,format='PNG')
    try:
        result=subprocess.run(['tesseract','stdin','stdout','--psm','11','--tessdata-dir',tessdata,
            '-l','eng','tsv'],input=raw.getvalue(),capture_output=True,timeout=30,check=True)
    except (OSError,subprocess.SubprocessError):return []
    import csv
    return [(r['text'],int(r['left']),int(r['top']),int(r['width']),int(r['height']))
        for r in csv.DictReader(io.StringIO(result.stdout.decode()),delimiter='\t')
        if r.get('text','').strip() and r.get('level')=='5']


def verify_candidates(doc,index,target,tessdata):
    result={k:index.get(k) for k in ('complete','scanned','candidates','pages')}
    result['hits']=[]
    candidates=[r for r in index.get('labels',[]) if exact_map_token(r['text'],target)]
    with OCRWorker() as worker:
        for candidate in candidates:
            page=doc[candidate['page_number']];readings=[];hashes=[];rects=[]
            for scale in (16,24,32):
                im=ImageOps.expand(_label_image(page.get_displaylist(),
                    {'rect':candidate['pdf_rect'],'angle':candidate.get('angle',0)},scale),border=30,fill='white')
                raw=io.BytesIO();im.save(raw,format='PNG')
                tokens=[w for w,_,_ in _ocr_words(im,tessdata,worker)]
                mode='MuPDF/Tesseract automatic'
                if candidate.get('origin')=='overlapping_map_tiles' or not any(exact_map_token(w,target) for w in tokens):
                    sparse=sparse_words(im,tessdata)
                    matching=[r for r in sparse if exact_map_token(r[0],target)]
                    if matching:
                        tokens=[r[0] for r in sparse];mode='Tesseract sparse text (PSM 11)'
                        if candidate.get('origin')=='overlapping_map_tiles' and len(matching)==1:
                            _,x,y,w,h=matching[0];r=fitz.Rect(candidate['pdf_rect'])+(-.35,-.35,.35,.35)
                            rects.append([(math.floor(r.x0*scale)+x-30)/scale,
                                (math.floor(r.y0*scale)+y-30)/scale,
                                (math.floor(r.x0*scale)+x+w-30)/scale,
                                (math.floor(r.y0*scale)+y+h-30)/scale])
                readings.append({'scale':scale,'tokens':tokens,'segmentation':mode})
                hashes.append(hashlib.sha256(raw.getvalue()).hexdigest())
                if not any(exact_map_token(w,target) for w in tokens):break
            if len(readings)==3 and all(any(exact_map_token(w,target) for w in r['tokens']) for r in readings):
                # Tile OCR boxes can contain cadastral linework as well as the
                # label. A correct string without three stable glyph boxes is
                # not a precise map location suitable for geometric anchors.
                if candidate.get('origin')=='overlapping_map_tiles' and len(rects)!=3:continue
                hit={**candidate,'method':'eredeti tervi HRSZ, három felbontásban ellenőrizve',
                    'source_readings':readings,'source_crop_sha256':hashes,
                    'normalization':'csak záró írásjelek; belső számok, nullák és utótagok változatlanok'}
                if len(rects)==3:
                    if max(math.dist(((a[0]+a[2])/2,(a[1]+a[3])/2),((b[0]+b[2])/2,(b[1]+b[3])/2))
                           for a in rects for b in rects)>1:continue
                    hit.update(index_rect_pdf=candidate['pdf_rect'],pdf_rect=rects[-1])
                result['hits'].append(hit)
    return result


def tiles(rect,side=600,overlap=40):
    step=side-overlap
    if step<=0:raise ValueError('A lapkák átfedése kisebb kell legyen a lapkaméretnél.')
    for y in range(math.ceil(rect.height/step)):
        for x in range(math.ceil(rect.width/step)):
            yield fitz.Rect(rect.x0+x*step,rect.y0+y*step,
                min(rect.x1,rect.x0+x*step+side),min(rect.y1,rect.y0+y*step+side))


def _boxes(worker,image,tessdata):
    raw=io.BytesIO();image.save(raw,format='PNG')
    import base64
    worker.process.stdin.write(json.dumps({'png':base64.b64encode(raw.getvalue()).decode(),
        'tessdata':tessdata,'boxes':True})+'\n');worker.process.stdin.flush()
    prefix='TELEK_OCR_RESULT:'
    for _ in range(64):
        line=worker.process.stdout.readline()
        if not line:raise RuntimeError('A térképi OCR-folyamat eredmény nélkül leállt.')
        if line.startswith(prefix):break
    else:raise RuntimeError('A térképi OCR-válasz nem azonosítható.')
    answer=json.loads(line[len(prefix):])
    if 'error' in answer:raise RuntimeError(answer['error'])
    return answer['words']


def tiled_index(doc,tessdata,*,max_seconds=600,cache_dir=None,on_progress=None):
    raw=doc.tobytes(no_new_id=True) if doc.is_dirty else (doc.stream or doc.tobytes(no_new_id=True))
    source=hashlib.sha256(raw).hexdigest()
    folder=Path(cache_dir or Path(tempfile.gettempdir())/'telekeloiras_tile_cache');folder.mkdir(exist_ok=True,parents=True)
    key=hashlib.sha256((source+INDEX_HASH).encode()).hexdigest();path=folder/(key+'.json')
    state={'source_sha256':source,'implementation':INDEX_HASH,'labels':[],
        'complete':False,'scanned':0,'next_page':0,'next_tile':0,'pages':0,'candidates':0}
    if path.is_file():
        saved=json.loads(path.read_text())
        if saved.get('source_sha256')==source and saved.get('implementation')==INDEX_HASH:state=saved
    if state['complete']:return state
    def save():
        with tempfile.NamedTemporaryFile(mode='w',dir=folder,delete=False) as output:
            json.dump(state,output);temporary=Path(output.name)
        temporary.replace(path)
    started=time.monotonic()
    with OCRWorker() as worker:
        for number in range(state['next_page'],len(doc)):
            page=doc[number];regions=list(tiles(page.rect))
            offset=state['next_tile'] if number==state['next_page'] else 0
            for i in range(offset,len(regions)):
                if time.monotonic()-started>max_seconds:save();return state
                clip=regions[i];scale=5
                pix=page.get_pixmap(matrix=fitz.Matrix(scale,scale),clip=clip,alpha=False)
                image=Image.frombytes('RGB',(pix.width,pix.height),pix.samples)
                for word,x0,y0,x1,y1 in _boxes(worker,image,tessdata):
                    token=word.strip('(),;:[]{}.')
                    if not re.fullmatch(r'\d+(?:[/-][A-Za-z0-9]+)*',token):continue
                    rect=[(pix.x+x0)/scale,(pix.y+y0)/scale,(pix.x+x1)/scale,(pix.y+y1)/scale]
                    center=((rect[0]+rect[2])/2,(rect[1]+rect[3])/2)
                    # Overlap duplicates are one inscription, not two parcels.
                    if any(r['page_number']==number and r['text']==token and
                           math.dist(center,((r['pdf_rect'][0]+r['pdf_rect'][2])/2,
                            (r['pdf_rect'][1]+r['pdf_rect'][3])/2))<3 for r in state['labels']):continue
                    state['labels'].append({'page_number':number,'pdf_rect':rect,'text':token,
                        'angle':0,'label_only':True,'coordinate_space':'display','origin':'overlapping_map_tiles'})
                state['scanned']+=1;state['candidates']=len(state['labels'])
                state['next_page']=number;state['next_tile']=i+1
                save()
                if on_progress:on_progress(number+1,len(doc),state['scanned'],len(state['labels']))
            state.update(next_page=number+1,next_tile=0,pages=number+1);save()
            fitz.TOOLS.store_shrink(100)
    state['complete']=True;save();return state


def repeated_sheet_labels(doc,index,hits,target,tessdata=None):
    """Identify a repeated atlas label from source neighbours and translation.

    Only display-label equivalence is established. No geographic registration,
    full parcel or zoning polygon is inferred from the atlas translation.
    """
    if len(hits)<2:return hits,{}
    reference=hits[0];page0=reference['page_number']
    def center(row):
        r=row['pdf_rect'];return ((r[0]+r[2])/2,(r[1]+r[3])/2)
    def neighbours(hit):
        rows={}
        for r in index.get('labels',[]):
            if r['page_number']==hit['page_number'] and math.dist(center(r),center(hit))<160 and r['text']!=target:
                rows.setdefault(r['text'],[]).append(r)
        return {key:value[0] for key,value in rows.items() if len(value)==1 and '/' in key}
    a=neighbours(reference);audits=[]
    for hit in hits[1:]:
        if hit['page_number']==page0:return hits,{'equivalent':False,'reason':'Külön felirat ugyanazon a tervlapon.'}
        b=neighbours(hit);common=sorted(a.keys()&b.keys());verified_neighbours={}
        if tessdata:
            # Bulk OCR boxes can include adjoining cadastral linework. They
            # are unsuitable alignment anchors until their glyph boxes have
            # been reread independently from the source at three resolutions.
            corrected_a={};corrected_b={}
            for key in common[:30]:
                verified=verify_candidates(doc,{'labels':[a[key],b[key]]},key,tessdata)['hits']
                left=[r for r in verified if r['page_number']==page0]
                right=[r for r in verified if r['page_number']==hit['page_number']]
                if len(left)!=1 or len(right)!=1:continue
                corrected_a[key]=left[0];corrected_b[key]=right[0]
                verified_neighbours[key]=[left[0],right[0]]
            local_a=corrected_a;local_b=corrected_b;common=sorted(local_a.keys()&local_b.keys())
        else:local_a=a;local_b=b
        shift=(center(hit)[0]-center(reference)[0],center(hit)[1]-center(reference)[1])
        supported=[key for key in common if math.dist((center(local_a[key])[0]+shift[0],center(local_a[key])[1]+shift[1]),center(local_b[key]))<2]
        # Require distributed neighbours, not repetitions of one OCR token.
        if len(supported)<5:return hits,{'equivalent':False,'shared_neighbours':supported}
        spread=max(math.dist(center(local_a[x]),center(local_a[y])) for x in supported for y in supported)
        if spread<30:return hits,{'equivalent':False,'reason':'A támpontok térbeli kiterjedése elégtelen.'}
        audits.append({'from_pdf_page':page0+1,'to_pdf_page':hit['page_number']+1,
            'translation_pdf':shift,'shared_neighbours':supported,
            'max_residual_pdf':max(math.dist((center(local_a[k])[0]+shift[0],center(local_a[k])[1]+shift[1]),center(local_b[k])) for k in supported),
            'verified_neighbours':{k:verified_neighbours[k] for k in supported if k in verified_neighbours},
            'target_verified_three_resolutions':True})
    # Prefer the complete local context rather than an inscription at a sheet edge.
    def margin(hit):
        r=fitz.Rect(hit['pdf_rect']);p=doc[hit['page_number']].rect
        return min(r.x0-p.x0,p.x1-r.x1,r.y0-p.y0,p.y1-r.y1)
    chosen=max(hits,key=margin)
    return [chosen],{'equivalent':True,'method':'azonos HRSZ és legalább öt egyező, térben eloszló szomszédos HRSZ',
        'comparisons':audits,'all_hits':hits,'geographic_registration_verified':False,
        'parcel_boundary_verified':False}


def verified_zone_labels(page,clip,pattern,tessdata):
    """Read whole source codes twice; numeric building parameters are excluded."""
    readings=[]
    with OCRWorker() as worker:
        for scale in (5,8):
            pix=page.get_pixmap(matrix=fitz.Matrix(scale,scale),clip=clip,alpha=False)
            image=Image.frombytes('RGB',(pix.width,pix.height),pix.samples)
            found=[]
            boxes=_boxes(worker,image,tessdata)
            boxes.extend((word,x,y,x+w,y+h) for word,x,y,w,h in sparse_words(image,tessdata))
            # Alternate text-only view; original RGB remains the map evidence.
            # No colours are interpreted as zoning semantics in this operation.
            import numpy as np
            pixels=np.asarray(image).copy()
            neutral=(pixels.max(2).astype(int)-pixels.min(2)<25)&(pixels.max(2)<190)
            pixels[~neutral]=255
            boxes.extend((word,x,y,x+w,y+h) for word,x,y,w,h in sparse_words(Image.fromarray(pixels),tessdata))
            for word,x0,y0,x1,y1 in boxes:
                token=word.strip('(),;:[]{}.')
                if pattern.fullmatch(token):
                    rect=fitz.Rect((pix.x+x0)/scale,(pix.y+y0)/scale,(pix.x+x1)/scale,(pix.y+y1)/scale)
                    if not any(code.casefold()==token.casefold() and other.intersects(rect) for code,other in found):
                        found.append((token,rect))
            readings.append(found)
    result=[(code,rect) for code,rect in readings[1] if any(
        other.casefold()==code.casefold() and rect.intersects(other_rect)
        for other,other_rect in readings[0])]
    # Dense linework can prevent segmentation at one full-context resolution.
    # Re-read each discovered inscription in a smaller original source crop.
    for code,rect in readings[0]:
        if any(c.casefold()==code.casefold() and r.intersects(rect) for c,r in result):continue
        pix=page.get_pixmap(matrix=fitz.Matrix(8,8),clip=(rect+(-1,-1,1,1))&page.rect,alpha=False)
        pixels=np.frombuffer(pix.samples,dtype=np.uint8).reshape(pix.height,pix.width,3).copy()
        neutral=(pixels.max(2).astype(int)-pixels.min(2)<25)&(pixels.max(2)<190)
        pixels[~neutral]=255
        words=sparse_words(ImageOps.expand(Image.fromarray(pixels),border=30,fill='white'),tessdata)
        if any(w.strip('(),;:[]{}.').casefold()==code.casefold() for w,*_ in words):result.append((code,rect))
    return result


def large_cad_codes(page,clip,pattern,tessdata):
    """Read larger filled CAD glyph groups missed by numeric parcel indexes."""
    groups=[];matrix=page.rotation_matrix
    def collect(d):
        colour=d.get('fill');r=fitz.Rect(d['rect'])*matrix
        if (colour is not None and max(colour)<.85 and max(colour)-min(colour)<.025
                and clip.contains(r) and 5<r.height<30 and .2<r.width<30):groups.append(r)
    page.get_cdrawings(callback=collect)
    merged=[]
    for rect in sorted(groups,key=lambda r:r.x0):
        partners=[i for i,r in enumerate(merged) if abs((r.y0+r.y1-rect.y0-rect.y1)/2)<max(r.height,rect.height)*.3
            and rect.x0-r.x1<max(r.height,rect.height)*.45 and r.x0<=rect.x1]
        if partners:
            index=partners[0];merged[index]|=rect
            for other in reversed(partners[1:]):merged[index]|=merged.pop(other)
        else:merged.append(rect)
    result=[]
    for rect in merged:
        if not 1.5<rect.width/max(rect.height,.01)<20:continue
        readings=[]
        for scale in (8,12):
            pix=page.get_pixmap(matrix=fitz.Matrix(scale,scale),clip=(rect+(-.5,-.5,.5,.5))&page.rect,alpha=False)
            import numpy as np
            a=np.frombuffer(pix.samples,dtype=np.uint8).reshape(pix.height,pix.width,3).copy()
            neutral=(a.max(2).astype(int)-a.min(2)<25)&(a.max(2)<190);a[~neutral]=255
            im=ImageOps.expand(Image.fromarray(a),border=30,fill='white')
            if not shutil.which('tesseract'):break
            raw=io.BytesIO();im.save(raw,format='PNG')
            try:
                run=subprocess.run(['tesseract','stdin','stdout','--psm','7','--tessdata-dir',tessdata,'-l','eng'],
                    input=raw.getvalue(),capture_output=True,check=True,timeout=30)
            except (OSError,subprocess.SubprocessError):break
            token=re.sub(r'\s+','',run.stdout.decode()).strip('(),;:[]{}.|')
            if not pattern.fullmatch(token):break
            readings.append(token)
        if len(readings)==2 and readings[0].casefold()==readings[1].casefold():result.append((readings[0],rect))
    return result


def nearby_hrsz_candidates(doc,index,hits,target,tessdata):
    """Look for repeated inscriptions locally before accepting uniqueness."""
    candidates=[]
    for hit in hits:
        page=doc[hit['page_number']];clip=(fitz.Rect(hit['pdf_rect'])+(-150,-150,150,150))&page.rect
        pix=page.get_pixmap(matrix=fitz.Matrix(8,8),clip=clip,alpha=False)
        import numpy as np
        a=np.frombuffer(pix.samples,dtype=np.uint8).reshape(pix.height,pix.width,3).copy()
        neutral=(a.max(2).astype(int)-a.min(2)<25)&(a.max(2)<190);a[~neutral]=255
        for word,x,y,w,h in sparse_words(Image.fromarray(a),tessdata):
            if not exact_map_token(word,target):continue
            rect=[(pix.x+x)/8,(pix.y+y)/8,(pix.x+x+w)/8,(pix.y+y+h)/8]
            if any(r['page_number']==page.number and fitz.Rect(r['pdf_rect']).intersects(fitz.Rect(rect)) for r in hits+candidates):continue
            candidates.append({'text':word,'pdf_rect':rect,'page_number':page.number,'angle':0,
                'label_only':True,'coordinate_space':'display','origin':'overlapping_map_tiles'})
    confirmed=verify_candidates(doc,{'labels':candidates},target,tessdata)['hits']
    return hits+confirmed


def source_zone_pattern(legal_text,fallback):
    """Extend syntax with literal codes present in this official legal source."""
    candidates=set(re.findall(r'(?<![\w/-])(?:[A-Za-zÁÉÍÓÖŐÚÜŰáéíóöőúüű]{1,8})'
        r'(?:[-/][A-Za-zÁÉÍÓÖŐÚÜŰáéíóöőúüű0-9.]{1,12})+(?![\w/-])',legal_text or ''))
    # The fallback accepts conventional numeric codes. Additional literal codes
    # need a zone-context occurrence; prose compounds are not map code defaults.
    allowed=[]
    for code in candidates:
        for occurrence in re.finditer(re.escape(code),legal_text):
            context=legal_text[max(0,occurrence.start()-120):occurrence.end()+120].casefold()
            if any(word in context for word in ('övezet','ovezet','övezeti','építési','napelem')):
                allowed.append(re.escape(code));break
    return re.compile('(?:'+fallback.pattern+'|'+('|'.join(allowed) if allowed else r'(?!)')+')',re.I)


# Index extraction and target verification have separate lifetimes. Editing a
# verifier must reread source crops, without rescanning an unchanged entire atlas.
import ast
_tree=ast.parse(Path(__file__).read_text())
INDEX_HASH=hashlib.sha256((''.join(ast.dump(n,include_attributes=False) for n in _tree.body
    if isinstance(n,ast.FunctionDef) and n.name in ('tiles','_boxes','tiled_index'))+
    Path(__file__).with_name('plan_ocr_worker.py').read_text()).encode()).hexdigest()
