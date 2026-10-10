"""Local visual plan reading; raster association is a hypothesis, not zoning proof."""
import hashlib
import io
import os
import re
import fitz
import numpy as np
from PIL import Image, ImageDraw
from shapely.geometry import Point, Polygon, LineString, box
from geopdf import page_registrations, to_pdf
from plan_legend import styles_for


def model_availability():
    # Presence is reported without printing values or issuing billable requests.
    return {'multimodal_api_configured':False,
            'credential_present':bool(os.environ.get('OPENAI_API_KEY')),
            'multimodal_model_verified':False,'external_ai_requests':0,
            'external_ai_cost':0,'method':'PDF natív szöveg + Tesseract OCR + helyi képfeldolgozás',
            'reason':'Nincs ellenőrzött multimodális modellkapcsolat; képfeldolgozó útvonal.'}


def supported_labels(rgb, origin, scale, parcel, labels, styles, source_barriers=()):
    """Compare every local code against several parcel-interior visual paths.

    Shared legend colours are deliberately conservative obstacles. Neither a
    clear ray nor absent coloured pixels can prove absence of a zoning boundary.
    No dotted gaps, corners, cadastral boundaries or clip edges are repaired.
    """
    colours=[]
    for style in styles:
        for field in ('colour','stroke','fill'):
            value=style.get(field)
            if value is not None and (max(value)<.98 or max(value)-min(value)>.08):
                colours.append(np.array(value)*255)
    if not colours:return []
    anchors=[]
    xmin,ymin,xmax,ymax=parcel.bounds
    for a in (.25,.5,.75):
        for b in (.25,.5,.75):
            point=Point(xmin+a*(xmax-xmin),ymin+b*(ymax-ymin))
            if parcel.contains(point):anchors.append((point.x,point.y))
    result=[]
    for code,rect in labels:
        start=((rect.x0+rect.x1)/2,(rect.y0+rect.y1)/2); clear=0; paths=[]
        for anchor in anchors:
            n=max(2,int(np.ceil(np.linalg.norm(np.subtract(anchor,start))*scale*2))+1)
            points=np.linspace(start,anchor,n)
            # Ignore only the inscription's own box, never arbitrary map masks.
            points=np.array([p for p in points if not rect.contains(fitz.Point(*p))])
            if not len(points):continue
            ray=LineString([points[0],anchor])
            if any(ray.intersects(barrier) for barrier in source_barriers):continue
            pixels=np.rint((points-np.asarray(origin))*scale).astype(int)
            if np.any(pixels<0) or np.any(pixels[:,0]>=rgb.shape[1]) or np.any(pixels[:,1]>=rgb.shape[0]):continue
            samples=rgb[pixels[:,1],pixels[:,0]].astype(float)
            blocked=any(np.any(np.linalg.norm(samples-colour,axis=1)<35) for colour in colours)
            if not blocked:
                clear+=1;paths.append({'start_pdf':list(start),'end_pdf':list(anchor)})
        result.append({'code':code,'label_rect_pdf':list(rect),'clear_paths':clear,
                       'label_inside_target_parcel':parcel.contains(Point(*start)),
                       'tested_anchors':len(anchors),'paths':paths})
    return result


def inspect_visual_plan(doc, plan_result, profile, zone_pattern, hrsz, *, identity=None):
    out={'status':'not_identifiable','zone':'','intersection_verified':False,
         'model':model_availability(),'reasons':[], 'source':profile.get('identity',{}),
         'nearby_parcel_labels':[],'street_labels':[],'candidate_labels':[]}
    if not plan_result.get('parcel_boundary_verified') or not plan_result.get('parcel_wkt'):
        out['reasons']=['Nincs igazolt telekgeometria a helyi vizuális kapcsolat ellenőrzéséhez.'];return out
    from shapely import wkt
    number=plan_result.get('pdf_page',0)-1
    if not 0<=number<len(doc):out['reasons']=['Hiányzó tervlapi telekhely.'];return out
    page=doc[number];world=wkt.loads(plan_result['parcel_wkt'])
    if page.rotation:
        out['reasons']=['A forgatott tervlap vizuális koordinátakapcsolata nincs ellenőrizve.'];return out
    registration=next((r for r in page_registrations(page) if r['world_frame'].covers(world.representative_point())),None)
    if registration is None:out['reasons']=['Nincs ellenőrizhető tervlapi koordinátakapcsolat.'];return out
    matrix=registration['matrix'];parcel=Polygon([to_pdf(p,matrix) for p in world.exterior.coords],
        [[to_pdf(p,matrix) for p in ring.coords] for ring in world.interiors])
    bounds=fitz.Rect(parcel.bounds);padding=max(bounds.width,bounds.height)*.75
    clip=(bounds+(-padding,-padding,padding,padding)) & page.rect
    scale=min(5,1800/max(clip.width,clip.height))
    pix=page.get_pixmap(matrix=fitz.Matrix(scale,scale),clip=clip,alpha=False)
    rgb=np.frombuffer(pix.samples,dtype=np.uint8).reshape(pix.height,pix.width,3)
    words=page.get_text('words',clip=clip);labels=[]
    for word in words:
        text=word[4]
        if zone_pattern.fullmatch(text):labels.append((text,fitz.Rect(word[:4])))
        if re.fullmatch(r'\(?\d+(?:/\d+)?\)?',text):
            out['nearby_parcel_labels'].append({'text':text,'rect_pdf':list(word[:4]),'adjacency_verified':False})
    for block in page.get_text('blocks',clip=clip):
        if any(term in block[4].lower() for term in ('utca','körút',' út',' tér')):
            out['street_labels'].append({'text':block[4].strip(),'rect_pdf':list(block[:4])})
    zone_styles=styles_for(profile,'zone_boundary')
    obstacle_styles=zone_styles+styles_for(profile,'regulatory_line')+styles_for(profile,'road_area')
    from plan_connections import native_dotted_boundaries
    from geopdf import legend_layer_paths
    markers=native_dotted_boundaries(page,zone_styles)
    local=box(*clip)
    source_barriers=[line for line in markers.get('lines',[]) if line.intersects(local)]
    for role in ('zone_boundary','regulatory_line'):
        source_barriers.extend(line for path in legend_layer_paths(page,profile,role)
            for line in path['segments'] if line.intersects(local))
    associations=supported_labels(rgb,(pix.x/scale,pix.y/scale),scale,parcel,labels,obstacle_styles,source_barriers)
    out['candidate_labels']=associations
    supported={a['code'] for a in associations if a['clear_paths']>=3}
    bound=profile.get('identity',{})
    expected=identity or bound
    legend_bound=bool(bound.get('source_hash') and zone_styles and all(bound.get(k)==expected.get(k)
        for k in ('plan_url','plan_hash','edition','ksh')))
    exact=any(w[4]==hrsz and parcel.contains(Point((w[0]+w[2])/2,(w[1]+w[3])/2)) for w in words)
    if legend_bound and exact and len(supported)==1 and not markers.get('unread_boundary'):
        out.update(status='probable',zone=next(iter(supported)))
    out.update(pdf_page=number+1,clip_pdf=list(clip),scale=scale,
        parcel_pdf_wkt=parcel.wkt,legend_bound=legend_bound,exact_hrsz_in_parcel=exact,
        local_source_boundary_segments=len(source_barriers),
        marker_variant_supported=markers.get('supported',False),
        context_image_sha256=hashlib.sha256(pix.tobytes('png')).hexdigest())
    out['reasons']=['A helyi képi kapcsolat nem zárt övezeti terület bizonyítéka.',
        'A szaggatott/pontozott jelek közti fehér képpontok nem igazolják az átjárhatóságot.',
        'A közeli telekfeliratok szomszédsága és a kataszteri–tervi környezet egyezése külön igazolandó.']
    if len(supported)>1:out['reasons'].append('Több eltérő övezeti feliratnak van helyi képi kapcsolata.')
    out['source_gaps']=plan_result.get('closure_audit',{}).get('topology',{}).get('open_endpoints',[])
    # Reproducible source illustration: overlay colours are not official symbols.
    canvas=Image.fromarray(rgb.copy());draw=ImageDraw.Draw(canvas)
    def xy(p):return ((p[0]-pix.x/scale)*scale,(p[1]-pix.y/scale)*scale)
    draw.line([xy(p) for p in parcel.exterior.coords],fill=(0,160,220),width=4)
    for line in source_barriers:
        if line.geom_type=='LineString':draw.line([xy(p) for p in line.coords],fill=(0,110,0),width=1)
    for item in associations:
        r=fitz.Rect(item['label_rect_pdf']);draw.rectangle([xy(r.tl),xy(r.br)],outline=(180,0,220),width=3)
        for path in item['paths'][:1]:draw.line([xy(path['start_pdf']),xy(path['end_pdf'])],fill=(180,0,220),width=2)
    for gap in out['source_gaps']:
        x,y=xy(gap['point_pdf']);draw.ellipse((x-7,y-7,x+7,y+7),outline=(0,90,255),width=3)
    buffer=io.BytesIO();canvas.save(buffer,format='PNG');out['annotated_png']=buffer.getvalue()
    out['annotation_note']='Cián: igazolt telek. Zöld: saját jelmagyarázathoz illesztett forrásszakasz. Lila: felirat és vizuális jelöltkapcsolat. Kék kör: eredeti nyitott csatlakozás. Nem hivatalos jelölések.'
    return out


def add_source_legend(result, profile, cache):
    """Append actual legend samples after checking the archived original bytes."""
    from pathlib import Path
    identity=profile.get('identity',{})
    path=Path(cache)/(hashlib.sha256(identity.get('source_url','').encode()).hexdigest()+'.pdf')
    if not result.get('annotated_png') or not path.is_file():return result
    if hashlib.sha256(path.read_bytes()).hexdigest()!=identity.get('source_hash'):return result
    excerpts=[]
    with fitz.open(path) as legend:
        for record in profile.get('records',[]):
            if record.get('role') not in ('zone_boundary','regulatory_line','road_area'):continue
            number=record.get('PDF-oldal',0)-1
            if not 0<=number<len(legend):continue
            rect=fitz.Rect(record['rect']) | fitz.Rect(record['sample_rect'])
            pix=legend[number].get_pixmap(matrix=fitz.Matrix(3,3),clip=rect+(-3,-3,3,3),alpha=False)
            excerpts.append(Image.open(io.BytesIO(pix.tobytes('png'))).convert('RGB'))
    if not excerpts:return result
    context=Image.open(io.BytesIO(result['annotated_png'])).convert('RGB')
    width=max(context.width,max(e.width for e in excerpts))
    image=Image.new('RGB',(width,context.height+sum(e.height+10 for e in excerpts)),(245,245,245))
    image.paste(context,(0,0));y=context.height
    for excerpt in excerpts:image.paste(excerpt,(0,y));y+=excerpt.height+10
    buffer=io.BytesIO();image.save(buffer,format='PNG');result['annotated_png']=buffer.getvalue()
    result['legend_image_source_verified']=True
    result['annotated_image_sha256']=hashlib.sha256(result['annotated_png']).hexdigest()
    return result
