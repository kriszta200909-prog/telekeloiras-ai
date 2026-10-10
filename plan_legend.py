"""Discover and persist each official plan's own visual vocabulary.

Only semantic Hungarian labels are shared. Colours, strokes, fills and marker
fonts are read from the adjacent source samples; they are never country defaults.
A parsed legend is not itself proof of parcel/zone membership.
"""
import hashlib
import base64
from difflib import SequenceMatcher
import io
import json
import math
import os
from pathlib import Path
import re
import tempfile
import time

import fitz
import numpy as np
from PIL import Image
from fontTools.ttLib import TTFont
from fontTools.pens.boundsPen import BoundsPen

from zone_parameters import compact


def digest(raw):return hashlib.sha256(raw).hexdigest()


IMPLEMENTATION_HASH=digest(Path(__file__).read_bytes())


def role_for_label(text):
    key=compact(text)
    if 'jelmagyarazat' in key:return 'legend_title'
    if 'megyei' in key or 'megyeteruletrendezesi' in key:return 'restriction'
    if 'veszelyessegiovezet' in key or any(word in key for word in ('natura2000','vedoterulet','vedosav','vedotavolsag','vedettterulet','hidrogeologia','vizbazis','nagyvizimeder')):return 'restriction'
    if re.search(r'ovezet(?:i)?hatar',key):return 'zone_boundary'
    if any(term in key for term in ('epitesiovezetiparameter','ovezetiparameter','szabalyozasijel','ovezetijel','ovezetjele','ovezetkod')):return 'zone_code'
    if ('szabalyozasivonal' in key or 'szabayozasivonal' in key):return 'regulatory_line'
    if 'banyatelek' in key:return 'restriction'
    if any(word in key for word in ('levezetosav','aramlasiholtter','partisav','tolteslab',
        'erozioerzekeny','alabanyaszott','regeszeti','muemleki','tajkepvedelmi',
        'okologiaihalozat','nemzetipark','beultetesikotelezettseg')):return 'restriction'
    if any(word in key for word in ('vezetek','kabel')) and any(word in key for word in ('villamos','foldgaz')):return 'utility_line'
    if 'epitesivonal' in key:return 'building_line'
    if 'telekhatartol' in key:return 'building_line' if 'vonal' in key else ''
    if 'foldreszlethatar' in key or 'telekhatar' in key:
        return 'proposed_parcel_boundary' if 'javasolt' in key or 'tervezett' in key else 'parcel_boundary'
    if 'tilalom' in key:return 'prohibition'
    if any(word in key for word in ('natura2000','vedoterulet','vedosav','vedotavolsag','vedettterulet','hidrogeologia','vizbazis','nagyvizimeder')):return 'restriction'
    if 'kozlekedesi' in key and 'terulet' in key:return 'road_area'
    if any(word in key for word in ('erdoterulet','zoldterulet','vizgazdalkodasiterulet')):return 'landuse_area'
    if 'teruletfelhasznalas' in key and ('hatar' in key or 'egysegek' in key):return 'landuse_boundary'
    return ''


def font_registry(page):
    result={};seen=set()
    for row in page.get_fonts(full=True):
        xref,extension=row[:2]
        if xref in seen or extension!='ttf':continue
        seen.add(xref)
        font=TTFont(io.BytesIO(page.parent.extract_font(xref)[3]))
        names={compact(row[3].split('+')[-1])}
        names.update(compact(font['name'].getDebugName(i) or '') for i in (1,4,6))
        for name in names:
            if name:result.setdefault(name,[]).append(font)
    return result


def glyph_description(font,index):
    name=font.getGlyphOrder()[index];glyph=font['glyf'][name]
    pen=BoundsPen(font.getGlyphSet());font.getGlyphSet()[name].draw(pen)
    if pen.bounds is None:raise ValueError('Üres jel.')
    x0,y0,x1,y1=pen.bounds;units=font['head'].unitsPerEm
    coordinates,endpoints,flags=glyph.getCoordinates(font['glyf'])
    signature=json.dumps({'coordinates':[[round(x/units,5),round(y/units,5)] for x,y in coordinates],
                          'endpoints':list(endpoints),'flags':list(flags)},sort_keys=True).encode()
    return {'glyph_hash':digest(signature),'centre':((x0+x1)/2/units,(y0+y1)/2/units),
            'bounds':(x0/units,y0/units,x1/units,y1/units),'contours':glyph.numberOfContours,
            'on_curve':[(x/units,y/units) for (x,y),flag in zip(coordinates,flags) if flag&1]}


def _colour(value):
    return [round(float(v),4) for v in value] if value is not None else None


def drawing_style(drawing):
    # Absolute colour is source data. Dash ratios are invariant to printing
    # scale, but solid and dashed strokes are always distinct.
    dash=drawing.get('dashes') or '[] 0'
    numbers=[float(v) for v in re.findall(r'[\d.]+',dash.partition(']')[0])]
    total=sum(numbers)
    result={'kind':'path','stroke':_colour(drawing.get('color')),
            'fill':_colour(drawing.get('fill')),'width':round(drawing.get('width') or 0,4),
            'dash':([round(v/total,4) for v in numbers] if total else []),
            'closed':bool(drawing.get('closePath') or any(i[0] in ('re','qu') for i in drawing['items']))}
    lines=[i for i in drawing['items'] if i[0]=='l']
    if (lines and len(lines)==len(drawing['items']) and drawing.get('fill') is None
            and all(math.dist(tuple(i[1]),tuple(i[2]))<=max(.2,2*(drawing.get('width') or 0)) for i in lines)):
        result['primitive']='short_dot_strokes'
    return result


def matches_style(actual, expected):
    if actual.get('kind')!=expected.get('kind'):return False
    if actual['kind']=='marker':
        return (actual.get('glyph_hash')==expected.get('glyph_hash')
                and actual.get('colour')==expected.get('colour')
                and .7<=actual.get('size',0)/max(expected.get('size',0),1e-9)<=1.4)
    if actual['kind']=='path':
        if expected.get('primitive') and actual.get('primitive')!=expected['primitive']:return False
        if expected.get('closed') and not actual.get('closed'):return False
        if expected.get('fill') is not None:
            return actual.get('fill')==expected['fill']
        if any(actual.get(key)!=expected.get(key) for key in ('stroke','fill','dash')):return False
        width=expected.get('width',0)
        return abs(actual.get('width',0)-width)<=max(.015,.25*width)
    return False


def caption_rows(page):
    rows=[]
    for block in page.get_text('dict',flags=fitz.TEXTFLAGS_DICT & ~fitz.TEXT_PRESERVE_IMAGES)['blocks']:
        block_rows=[]
        for line in block.get('lines',[]):
            candidates=[s for s in line['spans'] if len(compact(s['text']))>=6]
            if not candidates:continue
            caption_font=max(candidates,key=lambda s:len(compact(s['text'])))['font']
            spans=[s for s in line['spans'] if s['font']==caption_font]
            text=' '.join(s['text'] for s in spans);role=role_for_label(text)
            rect=fitz.Rect(spans[0]['bbox'])
            for span in spans[1:]:rect|=fitz.Rect(span['bbox'])
            block_rows.append({'label':text,'role':role,'rect':rect,'font':caption_font})
        index=0
        while index<len(block_rows):
            row=block_rows[index];index+=1
            if not row['role']:continue
            rect=row['rect'];height=rect.height;text=row['label']
            # A wrapped caption can be taller than the first text line; its
            # sample is centred on the entire caption. Native font boxes of
            # wrapped lines overlap substantially. Separate legend rows with
            # non-overlapping boxes must not be absorbed as continuations.
            if index<len(block_rows):
                continuation=block_rows[index];next_rect=continuation['rect']
                if (not continuation['role'] and continuation['font']==row['font']
                        and abs(next_rect.x0-rect.x0)<3
                        and .4*height<next_rect.y0-rect.y0<.8*height):
                    text+=' '+continuation['label'];rect|=next_rect;index+=1
            rows.append({'label':text,'role':row['role'],'rect':list(rect*page.rotation_matrix),'recognition':'native'})
    return rows


def ocr_rows(page,tessdata,clip=None,scale=None):
    clip=fitz.Rect(clip or page.rect)
    scale=scale or min(4,6000/max(clip.width,clip.height))
    pix=page.get_pixmap(matrix=fitz.Matrix(scale,scale),clip=clip,alpha=False)
    pix.set_dpi(round(72*scale),round(72*scale))
    with fitz.open(stream=pix.pdfocr_tobytes(language='eng',tessdata=tessdata),filetype='pdf') as doc:
        rows=caption_rows(doc[0])
        words=doc[0].get_text('words')
        prefixes=('jelmagyarazat','tervezett','meglevo','javasolt','epites','eptes','egyesitett','ovezet','ovezeti','szabalyozasi',
                  'foldreszlet','telekhatar','hidrogeologia','natura','vedoterulet','vedosav',
                  'vedotav','vizmukut','vizmokut','termalkut','orszagos','banyatelek')
        for row in rows:
            rect=fitz.Rect(row['rect'])
            caption_words=[w for w in words if rect.contains(fitz.Rect(w[:4]))]
            start=next((i for i,w in enumerate(caption_words) if compact(w[4]).startswith(prefixes)),None)
            if start is not None:
                remaining=caption_words[start:]
                row['label']=' '.join(w[4] for w in remaining)
                bounds=fitz.Rect(remaining[0][:4])
                for word in remaining[1:]:bounds|=fitz.Rect(word[:4])
                row['rect']=list(bounds)
    for row in rows:
        rect=fitz.Rect(row['rect'])
        # OCR PDF uses the explicitly set DPI. Pixmap origin, including its
        # integer rounding, maps the crop back to the source page.
        row['rect']=[v+offset for v,offset in zip(rect,(pix.x/scale,pix.y/scale)*2)]
        row['recognition']='OCR candidate'
    return rows


def sample_styles(page,rect):
    styles=[];registry=font_registry(page)
    # MuPDF temporarily exposes unrotated page state during drawing callbacks.
    # Freeze the transform beforehand, including for outlined CAD legends.
    rotation=page.rotation_matrix
    for trace in page.get_texttrace():
        if trace['type']!=0 or trace['opacity']!=1:continue
        fonts=registry.get(compact(trace['font']),[])
        if len(fonts)!=1:continue
        for char in trace['chars']:
            if not rect.contains(fitz.Rect(char[3])*page.rotation_matrix):continue
            try:description=glyph_description(fonts[0],char[1])
            except (ValueError,KeyError,IndexError,TypeError):continue
            styles.append({'kind':'marker','glyph_hash':description['glyph_hash'],
                           'colour':_colour(trace['color']),'size':round(trace['size'],4)})
    drawings=[]
    def collect(drawing):
        bounds=(fitz.Rect(drawing['rect'])*rotation)+(-.01,-.01,.01,.01)
        if rect.intersects(bounds) and rect.contains(bounds):drawings.append((drawing,bounds))
    page.get_cdrawings(callback=collect)
    chromatic=[bounds for drawing,bounds in drawings if any(
        max(c)-min(c)>.08 for c in (drawing.get('fill'),drawing.get('color')) if c)]
    for drawing,bounds in drawings:
        style=drawing_style(drawing)
        if style['fill'] is not None and min(style['fill'])>.99:continue
        neutral=all(max(c)-min(c)<.025 for c in (style['fill'],style['stroke']) if c)
        rectangle=all(item[0]=='re' for item in drawing['items'])
        # A rectangular container around a coloured sample is not a standalone
        # line symbol. Retain black-only symbols, including rectangular ones.
        container=(neutral and rectangle and any(bounds.contains(other) and
            bounds.height>other.height+.5 and bounds.width>other.width+.5 for other in chromatic))
        edge_fragment=(neutral and rectangle and chromatic and max(bounds.width,bounds.height)<1 and
            min(abs(bounds.x0-rect.x0),abs(bounds.x1-rect.x1))<2)
        if container or edge_fragment:continue
        if bounds.width>.5 or bounds.height>.5:styles.append(style)
    return list({json.dumps(s,sort_keys=True):s for s in styles}.values())


def raster_sample(page, rect):
    """Preserve a scanned legend sample without guessing its vector meaning.

    Background is measured from the sample border, never assumed white.
    This descriptor is intentionally not accepted by the vector matcher.
    """
    if rect.is_empty:return []
    pix=page.get_pixmap(matrix=fitz.Matrix(4,4),clip=rect,alpha=False)
    pixels=np.asarray(Image.frombytes('RGB',(pix.width,pix.height),pix.samples))
    if min(pixels.shape[:2])<3:return []
    border=np.concatenate((pixels[0],pixels[-1],pixels[:,0],pixels[:,-1]))
    quantized=(border//16)*16
    colours,counts=np.unique(quantized,axis=0,return_counts=True)
    background=colours[counts.argmax()].astype(float)+7.5
    difference=np.linalg.norm(pixels.astype(float)-background,axis=2)
    foreground=difference>45
    occupancy=float(foreground.mean())
    if not .01<occupancy<.65:return []
    foreground_colours=(pixels[foreground]//16)*16
    colours,counts=np.unique(foreground_colours,axis=0,return_counts=True)
    dominant=colours[counts.argmax()]
    raw=pix.tobytes('png')
    return [{'kind':'raster_sample','sample_sha256':digest(raw),
             'png_base64':base64.b64encode(raw).decode('ascii'),
             'background_rgb':background.round(1).tolist(),
             'foreground_rgb':dominant.tolist(),'foreground_fraction':round(occupancy,4),
             'pixel_size':[pix.width,pix.height],
             'geometry_matching_supported':False}]


def parse_legend(page,rows):
    title=any(row['role']=='legend_title' for row in rows)
    result=[]
    if not title:return result
    captions=[r for r in rows if r['role']!='legend_title']
    # Some CAD legends have OCR captions and a distant, framed sample column.
    # Locate the actual frames; a fixed left/right offset misses such layouts.
    frames=[];rotation=page.rotation_matrix
    def frame(d):
        colour=d.get('color');r=fitz.Rect(d['rect'])*rotation
        if (colour is not None and max(colour)-min(colour)<.025 and max(colour)<.85
                and all(i[0]=='re' for i in d['items']) and 12<r.width<100 and 5<r.height<45):
            frames.append(r)
    page.get_cdrawings(callback=frame)
    def associated(row,box):
        r=fitz.Rect(row['rect'])
        return (not r.intersects(box) and abs((r.y0+r.y1-box.y0-box.y1)/2)<max(r.height,box.height)/2
                and min(abs(r.x0-box.x1),abs(box.x0-r.x1))<page.rect.width*.2)
    column_support={}
    for row in captions:
        rect=fitz.Rect(row['rect']);height=rect.height
        # Find the sample immediately to the left of this caption. A semantic
        # match without a spatially associated sample remains unsupported.
        sample=fitz.Rect(max(page.rect.x0,rect.x0-min(60,page.rect.width*.08)),
                         rect.y0-.15*height,rect.x0-1,rect.y1+.15*height)
        styles=sample_styles(page,sample)
        if not styles and row['recognition']!='native':styles=raster_sample(page,sample)
        framed=[]
        if row['recognition']!='native' and (not styles or all(s.get('kind')=='raster_sample' for s in styles)):
            for candidate in frames:
                if not associated(row,candidate):continue
                key=(candidate.x0,candidate.height)
                if key not in column_support:
                    positions=sorted((fitz.Rect(other['rect']).y0+fitz.Rect(other['rect']).y1)/2 for other in captions
                        if any(abs(box.x0-candidate.x0)<3 and associated(other,box) for box in frames))
                    supporting=[]
                    for y in positions:
                        if not supporting or y-supporting[-1]>candidate.height*.5:supporting.append(y)
                    column_support[key]=len(supporting)
                if column_support[key]>=3:framed.append((candidate,column_support[key]))
        sample_basis={}
        if len(framed)==1:
            sample=framed[0][0]+(-.1,-.1,.1,.1)
            styles=sample_styles(page,sample) or raster_sample(page,sample)
            sample_basis={'method':'source_rectangle_column','aligned_caption_rows':framed[0][1]}
        right=fitz.Rect(rect.x1+1,rect.y0-.15*height,
                        min(page.rect.x1,rect.x1+min(60,page.rect.width*.08)),rect.y1+.15*height)
        neighbour_column=any(other is not row
                             and rect.x1<fitz.Rect(other['rect']).x0<right.x1+60
                             and fitz.Rect(other['rect']).y0<right.y1
                             and fitz.Rect(other['rect']).y1>right.y0
                             for other in captions)
        # OCR can include neighbouring captions on the right. Only a native
        # caption permits automatic reversal of sample placement.
        right_styles=sample_styles(page,right) if not right.is_empty and row['recognition']=='native' and not neighbour_column else []
        left_styles=list(styles)
        ambiguous=bool(len(framed)>1 or (styles and right_styles and styles!=right_styles))
        if not styles and right_styles:sample=right;styles=right_styles
        if ambiguous:styles=[]
        # For OCR captions independently reread the caption crop at a second
        # scale. A single uncertain recognition may be displayed, not trusted.
        stable=row['recognition']=='native'
        result.append({**row,'sample_rect':list(sample),'styles':styles,
                       'label_verified':stable,'ambiguous_sample':ambiguous,'PDF-oldal':page.number+1,
                       'sample_column_evidence':sample_basis,
                       '_left_styles':left_styles if ambiguous else [],
                       '_right_styles':right_styles,'_right_rect':list(right)})
    # Multi-column legends can place the next column's graphic immediately
    # after a long caption. Infer placement only from at least three aligned,
    # unambiguous native rows in this very legend; a lone two-sided sample
    # remains ambiguous. No municipality or national placement is assumed.
    for record in result:
        if record['ambiguous_sample'] and record['recognition']=='native':
            x=record['rect'][0]
            aligned=[other for other in result if other is not record
                     and other['recognition']=='native' and not other['ambiguous_sample']
                     and other['styles'] and abs(other['rect'][0]-x)<8]
            left=sum(other['sample_rect'][2]<other['rect'][0] for other in aligned)
            right=sum(other['sample_rect'][0]>other['rect'][2] for other in aligned)
            if left>=3 and right==0:
                record['styles']=record['_left_styles'];record['ambiguous_sample']=False
                record['sample_side_basis']={'side':'left','aligned_native_rows':left}
            elif right>=3 and left==0:
                record['styles']=record['_right_styles'];record['sample_rect']=record['_right_rect']
                record['ambiguous_sample']=False
                record['sample_side_basis']={'side':'right','aligned_native_rows':right}
        for key in ('_left_styles','_right_styles','_right_rect'):record.pop(key)
    return result


def discover_legend(doc,*,source_url,source_hash,plan_url,plan_hash,edition,ksh,
                    tessdata=None,max_seconds=180,cache_dir=None,on_progress=None):
    cache=Path(cache_dir or os.environ.get('TELEKELOIRAS_LEGEND_CACHE_DIR') or (Path(tempfile.gettempdir())/'telekeloiras_legends'))
    implementation=IMPLEMENTATION_HASH
    identity={'source_url':source_url,'source_hash':source_hash,'plan_url':plan_url,
              'plan_hash':plan_hash,'edition':edition,'ksh':str(ksh),'implementation':implementation}
    key=digest(json.dumps(identity,sort_keys=True).encode());path=cache/(key+'.json')
    if path.is_file():
        try:
            cached=json.loads(path.read_text());seal=cached.pop('content_hash')
            if cached['identity']==identity and seal==digest(json.dumps(cached,sort_keys=True).encode()):
                cached['cache_hit']=True;return cached
        except (ValueError,KeyError,TypeError):pass
    started=time.monotonic();records=[];scanned=0
    native_pages=[p.number for p in doc if any(r['role']=='legend_title' for r in caption_rows(p))]
    coarse_pages=[]
    if not native_pages and tessdata:
        # Prioritisation only: a missed thumbnail title never proves absence.
        # Reading a complete dense CAD map at full resolution can take minutes.
        for page in doc:
            if time.monotonic()-started>min(max_seconds*.25,45):break
            thumbnail=ocr_rows(page,tessdata,scale=min(2,2000/max(page.rect.width,page.rect.height)))
            if any(row['role']=='legend_title' for row in thumbnail):coarse_pages.append(page.number)
    # A thumbnail can miss the tiny title of a detailed first-sheet legend
    # while finding a simpler legend on another sheet. Always inspect the
    # first source sheet before allowing an OCR-only title to reorder it.
    preferred=native_pages if native_pages else list(dict.fromkeys([0]+coarse_pages))
    page_order=preferred+[i for i in range(len(doc)) if i not in preferred]
    for page_index in page_order:
        page=doc[page_index]
        if time.monotonic()-started>max_seconds:break
        rows=caption_rows(page)
        if not any(r['role']=='legend_title' for r in rows) and tessdata:
            rows=ocr_rows(page,tessdata)
        if any(r['role']=='legend_title' for r in rows):
            if any(r['recognition']!='native' for r in rows) and tessdata:
                headers=[r for r in rows if r['role']=='legend_title']
                for header in headers:
                    rect=fitz.Rect(header['rect']);width=min(700,page.rect.width*.45)
                    if rect.y0>page.rect.height*.7:
                        # A footer may name a complete legend sheet; its symbols
                        # are above it and may span several columns.
                        crop=page.rect;second_scale=min(3,7000/max(page.rect.width,page.rect.height))
                    else:
                        crop=fitz.Rect(max(0,(rect.x0+rect.x1-width)/2),0,
                                       min(page.rect.width,(rect.x0+rect.x1+width)/2),page.rect.height)
                    initial_scale=min(4,6000/max(page.rect.width,page.rect.height))
                    second_scale=min(3 if initial_scale==4 else 4,7000/max(crop.width,crop.height))
                    precise=ocr_rows(page,tessdata,crop,scale=second_scale)
                    # Match caption semantics and position twice, never accept
                    # an OCR confidence score as independent confirmation.
                    for row in precise:
                        if any(other['role']==row['role']
                               and fitz.Rect(other['rect']).intersects(fitz.Rect(row['rect']))
                               and SequenceMatcher(None,compact(other['label']),compact(row['label'])).ratio()>=.65
                               for other in rows):
                            row['recognition']='two-scale OCR'
                    rows.extend(precise)
            parsed=parse_legend(page,rows)
            for row in parsed:
                if row['recognition']=='two-scale OCR':row['label_verified']=True
            records.extend(parsed)
            if any(r['role']=='zone_boundary' and r['label_verified'] and r['styles'] for r in parsed):
                scanned+=1;break
        scanned+=1
        if on_progress:on_progress(scanned,len(doc),len(records))
        fitz.TOOLS.store_shrink(100)
    result={'identity':identity,'records':records,'cache_hit':False,'pages_scanned':scanned,
            'scan_complete':scanned==len(doc),
            'zone_style_verified':any(r['role']=='zone_boundary' and r['label_verified'] and r['styles'] for r in records)}
    # Only reusable successfully processed legends are cached. A deadline or
    # a source failure must be retried, not permanently stored as absence.
    if result['zone_style_verified']:
        cache.mkdir(parents=True,exist_ok=True);sealed=dict(result)
        sealed['content_hash']=digest(json.dumps(result,sort_keys=True).encode())
        with tempfile.NamedTemporaryFile(mode='w',encoding='utf-8',dir=cache,delete=False) as output:
            json.dump(sealed,output,ensure_ascii=False);temporary=Path(output.name)
        temporary.replace(path)
    return result


def styles_for(profile,role):
    return [style for record in (profile or {}).get('records',[])
            if record['role']==role and record.get('label_verified') for style in record['styles']]
