"""Free, offline multimodal trial; model opinions cannot establish legal zoning."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import threading

MODELS = {
    'qwen3-vl-2b': {'model':'Qwen/Qwen3-VL-2B-Instruct',
        'revision':'89644892e4d85e24eaac8bacfd4f463576704203',
        'weight_sha256':'7de1838c87a5349b016c26a1c3f7d2bc400a3d485f95ef39a7059ffd734977a0'},
    'smolvlm-500m': {'model':'HuggingFaceTB/SmolVLM-500M-Instruct',
        'revision':'a7da5b986cb59b408707209984f360a5f4ad7e47',
        'weight_sha256':'d05b567eeaf534e83d375551f068ed57b5f52d37c657197f644af5ef9db091a2'},
}
DEFAULT_MODEL = 'qwen3-vl-2b'
ROOT = Path(__file__).resolve().parent
ENV = ROOT/'work/local-vlm-env'
_MODEL_LOCK = threading.Lock()
IMPLEMENTATION_SHA256 = hashlib.sha256(Path(__file__).read_bytes()).hexdigest()


def model_spec(key):
    spec=dict(MODELS[key]);spec['path']=ROOT/'work/models'/key
    return spec


def cpu_threads():
    """Respect cgroup quota instead of oversubscribing visible host CPUs."""
    count=min(3,os.cpu_count() or 1)
    try:
        quota,period=Path('/sys/fs/cgroup/cpu.max').read_text().split()
        if quota!='max':count=min(count,max(1,int(quota)//int(period)))
    except (OSError,ValueError):pass
    return count


def prompt(hrsz):
    # English improves instruction-following in the tiny model. No OCR/reference code.
    return (f'Inspect these official zoning-plan images. Find parcel number {hrsz}. '
        'Image 1 is the unannotated plan crop; other images are its own official legend. '
        'Read the zone code belonging to the parcel, not just the nearest label. '
        'Use only this legend to distinguish parcel lines, zoning boundaries and restrictions. '
        'Do not invent connections across missing corners. If unreadable, use an empty zone. '
        'Return JSON with keys: hrsz, parcel_identified (boolean), zone (string), '
        'boundary_state (closed/open/unclear), visual_evidence (list of strings), '
        'uncertainties (list of strings). Use at most two short evidence items and two uncertainties. No legal proof can be inferred.')


def availability(model_key=DEFAULT_MODEL):
    spec=model_spec(model_key);model_dir=spec['path']
    metadata=model_dir/'.cache/huggingface/download/model.safetensors.metadata'
    pinned=metadata.is_file() and metadata.read_text().splitlines()[:2]==[spec['revision'],spec['weight_sha256']]
    return {'available': (ENV/'bin/python').is_file() and (model_dir/'model.safetensors').is_file() and pinned,
            'model': spec['model'], 'revision': spec['revision'], 'license': 'Apache-2.0',
            'external_ai_cost': 0, 'external_ai_requests': 0, 'device': 'CPU',
            'note': 'Helyi modell, nem jogi vagy geometriai igazolás.'}


def source_images(doc, visual, profile, cache):
    import fitz
    from visual_plan import add_source_legend
    if not (visual.get('legend_bound') and visual.get('legend_image_source_verified')):
        raise ValueError('Nem igazolt a terv és saját jelmagyarázat kapcsolata.')
    page = doc[visual['pdf_page']-1]
    clip = fitz.Rect(visual['clip_pdf'])
    scale = min(5,1800/max(clip.width,clip.height))
    raw = page.get_pixmap(matrix=fitz.Matrix(scale,scale),clip=clip,alpha=False).tobytes('png')
    if hashlib.sha256(raw).hexdigest() != visual.get('context_image_sha256'):
        raise ValueError('Az eredeti tervkivágat lenyomata eltér.')
    detail = add_source_legend({'annotated_png':raw},profile,cache)
    if not detail.get('legend_image_source_verified') or not detail.get('legend_png'):
        raise ValueError('Hiányzik az ellenőrzött saját jelmagyarázat képe.')
    images=[raw]
    identity=profile['identity']
    path=Path(cache)/(hashlib.sha256(identity['source_url'].encode()).hexdigest()+'.pdf')
    with fitz.open(path) as legend:
        for number in sorted({r['PDF-oldal'] for r in profile['records']}):
            page=legend[number-1]
            scale=min(3,1800/max(page.rect.width,page.rect.height))
            images.append(page.get_pixmap(matrix=fitz.Matrix(scale,scale),alpha=False).tobytes('png'))
    return images+[detail['legend_png']]


def compare(result, control, hrsz):
    result.update(intersection_verified=False, legal_classification_verified=False,
                  external_ai_requests=0, external_ai_cost=0)
    raw = result.get('raw_answer','')
    try:
        start,end = raw.index('{'),raw.rindex('}')+1
        answer = json.loads(raw[start:end])
        if (not isinstance(answer,dict) or not isinstance(answer.get('zone'),str)
                or answer.get('hrsz')!=hrsz or not isinstance(answer.get('parcel_identified'),bool)
                or answer.get('boundary_state') not in ('closed','open','unclear')
                or not all(isinstance(answer.get(k),list) and all(isinstance(v,str) for v in answer[k])
                           for k in ('visual_evidence','uncertainties'))):
            raise ValueError('Unstructured or wrong parcel answer')
        result.update(answer=answer, structured_answer_valid=True,
            reported_zone=answer['zone'], zone_suggestion=answer['zone'] if answer['parcel_identified'] else '',
            agrees_with_local_zone=bool(answer['parcel_identified']) and answer['zone']==control.get('zone'),
            zone_in_local_candidates=answer['zone'] in {r['code'] for r in control.get('candidate_labels',[])},
            closed_boundary_claim_verified=False,
            boundary_claim_requires_gap_review=answer['boundary_state']=='closed' and bool(control.get('source_gaps')))
    except (ValueError,TypeError,KeyError):
        result.update(structured_answer_valid=False,zone_suggestion='',agrees_with_local_zone=False,
            reason='Valódi modellválasz, de nem érvényes, ellenőrizhető telek-/övezeti állítás.')
    return result


def _run_local(images, hrsz, control, timeout=900, model_key=DEFAULT_MODEL):
    if not availability(model_key)['available']:
        return {'status':'not_run', **availability(model_key), 'intersection_verified':False,
                'reason':'A helyi ingyenes modell még nincs telepítve.'}
    (ROOT/'work').mkdir(exist_ok=True)
    with tempfile.TemporaryDirectory(dir=ROOT/'work',prefix='local-vision-') as folder:
        folder=Path(folder)
        paths=[]
        for i,raw in enumerate(images):
            path=folder/f'image-{i}.png';path.write_bytes(raw);paths.append(str(path))
        job={'model_key':model_key,'jobs':[{'hrsz':hrsz,'images':paths}], 'output':str(folder/'result.json')}
        (folder/'job.json').write_text(json.dumps(job))
        env={**os.environ,'HF_HUB_OFFLINE':'1','TRANSFORMERS_OFFLINE':'1','HF_HUB_DISABLE_TELEMETRY':'1',
             'OMP_THREAD_LIMIT':str(cpu_threads()),'OMP_NUM_THREADS':str(cpu_threads()),'MKL_NUM_THREADS':str(cpu_threads())}
        try:
            proc=subprocess.run([str(ENV/'bin/python'),str(Path(__file__).resolve()),'--worker',str(folder/'job.json')],
                env=env,stdout=subprocess.PIPE,stderr=subprocess.PIPE,timeout=timeout,check=False)
            if proc.returncode!=0 or not (folder/'result.json').is_file():
                raise ValueError('Local worker failed')
            receipt=json.loads((folder/'result.json').read_text())
            result=receipt['cases'][0]
            result.update({k:v for k,v in receipt.items() if k!='cases'})
            return compare(result,control,hrsz)
        except (OSError,ValueError,subprocess.TimeoutExpired):
            return {'status':'failed','reason':'Helyi modellhiba vagy időkorlát; a helyi OCR megmarad.',
                'intersection_verified':False,'external_ai_requests':0,'external_ai_cost':0}


def run_local(images, hrsz, control, timeout=900, model_key=DEFAULT_MODEL):
    if not _MODEL_LOCK.acquire(blocking=False):
        return {'status':'busy','reason':'Másik helyi modellfuttatás vagy telepítés folyamatban van. Próbáld meg a befejezése után.',
                'external_ai_requests':0,'external_ai_cost':0,'intersection_verified':False}
    try:
        return _run_local(images,hrsz,control,timeout,model_key)
    finally:
        _MODEL_LOCK.release()


def render_free_evidence(evidence):
    """Widgets run outside the cached investigation; source files stay verified."""
    import streamlit as st
    import fitz
    plan=evidence.get('plan',{})
    if not plan.get('current_verified') or not evidence.get('legal_source_verified'):
        return
    cache=Path(tempfile.gettempdir())/'telekeloiras_pdf_cache'
    path=cache/(hashlib.sha256(plan.get('source_url','').encode()).hexdigest()+'.pdf')
    if not path.is_file() or hashlib.sha256(path.read_bytes()).hexdigest()!=plan.get('source_hash'):
        st.info('A helyi AI-próbához az eredeti, ellenőrzött forrásterv szükséges.')
        return
    with fitz.open(path) as doc:
        render_free_review(doc,evidence['visual'],evidence['legend'],cache,evidence['hrsz'])


def render_free_review(doc, visual, profile, cache, hrsz):
    import streamlit as st
    with st.expander('Ingyenes helyi AI-vizsgálat',expanded=False):
        st.caption('Ingyenes nyílt súlyú képes modell: a képek helyben maradnak. A modell tévedhet; a helyi kontrollt és jogi minősítést nem módosítja.')
        model_key=st.selectbox('Helyi képes modell',list(MODELS),format_func=lambda key:MODELS[key]['model'])
        spec=model_spec(model_key)
        if not availability(model_key)['available']:
            st.info('A helyi modell nincs telepítve. Ingyenes letöltés: SmolVLM: kb. 1 GB letöltés, Qwen: kb. 4,3 GB; Qwen CPU-futtatáshoz legalább 8 GB rendszermemória ajánlott.')
            if st.button('Ingyenes helyi modell telepítése'):
                try:
                    with st.spinner('Az ingyenes helyi modell telepítése…'):setup(model_key)
                    st.rerun()
                except RuntimeError as error:st.info(str(error))
                except (OSError,subprocess.SubprocessError):st.error('A helyi telepítés nem sikerült; a hagyományos felismerés használható.')
            return
        if not (visual.get('legend_bound') and visual.get('legend_image_source_verified') and visual.get('clip_pdf')):
            st.info('Előbb a telek környezete és saját hivatalos jelmagyarázata szükséges.')
            return
        key='local-vlm-'+hashlib.sha256((hrsz+visual['context_image_sha256']+spec['revision']).encode()).hexdigest()
        if st.button('Tervrészlet és jelmagyarázat elemzése ingyenes AI-val',key=key+'-button'):
            try:
                with st.spinner('Helyi képelemzés CPU-n; CPU-n akár 10–15 percig tarthat…'):
                    st.session_state[key]=run_local(source_images(doc,visual,profile,cache),hrsz,visual,model_key=model_key)
            except (ValueError,OSError):st.error('A forrásképek ellenőrzése nem sikerült; AI-vizsgálat nem indult.')
        if key in st.session_state:
            result=st.session_state[key]
            if result.get('structured_answer_valid') and result.get('zone_suggestion'):
                if result.get('zone_in_local_candidates'):
                    st.write('A modell övezeti jelöltje: '+result['zone_suggestion'])
                else:
                    st.warning('A modell által olvasott jel nincs alátámasztva a helyi tervfeliratokkal: '+result['zone_suggestion']+' — övezeti besorolásként nem fogadható el.')
                st.write('Egyezés a helyi felismeréssel: '+('igen' if result['agrees_with_local_zone'] else 'nem'))
                st.write('A döntés képi indokai:',result['answer']['visual_evidence'])
                st.write('A modell bizonytalanságai:',result['answer']['uncertainties'])
                if result.get('boundary_claim_requires_gap_review'):
                    st.warning('A modell zárt övezethatárt állít, de a környezetben megszakadó forrásszakaszok is vannak. A zártság külön geometriai ellenőrzés nélkül nem fogadható el.')
            else:
                st.info(result.get('reason','A modell nem adott elfogadható telekhez kapcsolt övezeti jelöltet.'))
            with st.expander('Nyers modellválasz és ellenőrzési adatok'):
                st.json(result)
            st.warning('Kísérleti modellvélemény. Sem az egyezés, sem a modell határzártsági állítása nem jogi vagy geometriai bizonyítás.')


def _setup(model_key=DEFAULT_MODEL):
    """UI installation: public Apache model download only, no AI API/key/subscription."""
    import venv
    spec=model_spec(model_key)
    venv.EnvBuilder(with_pip=True).create(ENV)
    python=str(ENV/'bin/python')
    subprocess.run([python,'-m','pip','install','torch==2.8.0','torchvision==0.23.0','--index-url','https://download.pytorch.org/whl/cpu'],check=True)
    subprocess.run([python,'-m','pip','install','transformers==4.57.6','pillow','huggingface-hub<1'],check=True)
    env={**os.environ,'HF_HOME':str(ROOT/'work/hf-cache'),'HF_XET_CACHE':str(ROOT/'work/hf-cache/xet'),
         'HF_HUB_DISABLE_TELEMETRY':'1'}
    code=("from huggingface_hub import snapshot_download; snapshot_download("+repr(spec['model'])+
        ',revision='+repr(spec['revision'])+',local_dir='+repr(str(spec['path']))+
        ",allow_patterns=['*.json','*.txt','model.safetensors','README.md'],max_workers=2)")
    subprocess.run([python,'-c',code],env=env,check=True)
    return availability(model_key)


def setup(model_key=DEFAULT_MODEL):
    if not _MODEL_LOCK.acquire(blocking=False):
        raise RuntimeError('Másik helyi modellfuttatás vagy telepítés folyamatban van.')
    try:
        return _setup(model_key)
    finally:
        _MODEL_LOCK.release()


def worker(job_path):
    # Only local files are passed. trust_remote_code=False; offline env set by caller.
    import resource
    import torch
    import transformers
    from transformers import AutoProcessor, AutoModelForImageTextToText
    from PIL import Image
    torch.set_num_threads(cpu_threads())
    job=json.loads(Path(job_path).read_text())
    model_key=job.get('model_key',DEFAULT_MODEL);spec=model_spec(model_key);model_dir=spec['path']
    started=time.monotonic()
    digest=hashlib.sha256()
    with (model_dir/'model.safetensors').open('rb') as weights:
        for block in iter(lambda:weights.read(1024*1024),b''):digest.update(block)
    if digest.hexdigest()!=spec['weight_sha256']:raise ValueError('Changed model weights')
    processor=AutoProcessor.from_pretrained(model_dir,use_fast=False,local_files_only=True,trust_remote_code=False)
    if model_key=='smolvlm-500m':
        processor.image_processor.size={'longest_edge':1024}
        processor.image_processor.do_image_splitting=True
    else:
        processor.image_processor.size={'shortest_edge':65536,'longest_edge':1048576}
    model=AutoModelForImageTextToText.from_pretrained(model_dir,local_files_only=True,
        trust_remote_code=False,dtype=torch.bfloat16,attn_implementation='sdpa').eval()
    loaded=time.monotonic()-started
    receipt={'model':spec['model'],'revision':spec['revision'],'license':'Apache-2.0','device':'CPU','dtype':'bfloat16',
             'implementation_sha256':IMPLEMENTATION_SHA256,'weight_sha256':digest.hexdigest(),'torch':torch.__version__,'transformers':transformers.__version__,'cpu_threads':torch.get_num_threads(),'load_seconds':round(loaded,3),'cases':[]}
    for entry in job['jobs']:
        start=time.monotonic()
        images=[Image.open(p).convert('RGB') for p in entry['images']]
        text=prompt(entry['hrsz'])
        messages=[{'role':'user','content':[{'type':'image'} for _ in images]+[{'type':'text','text':text}]}]
        rendered=processor.apply_chat_template(messages,add_generation_prompt=True)
        inputs=processor(text=rendered,images=images,return_tensors='pt')
        inputs['pixel_values']=inputs['pixel_values'].to(torch.bfloat16)
        with torch.inference_mode():
            generated=model.generate(**inputs,max_new_tokens=192,do_sample=False)
        new=generated[:,inputs['input_ids'].shape[1]:]
        raw=processor.batch_decode(new,skip_special_tokens=True)[0]
        receipt['cases'].append({'hrsz':entry['hrsz'],'status':'completed','raw_answer':raw,
            'prompt':text,'image_sha256':[hashlib.sha256(Path(p).read_bytes()).hexdigest() for p in entry['images']],
            'preprocessing':{'size':processor.image_processor.size,'image_splitting':getattr(processor.image_processor,'do_image_splitting',False),'pixel_tensor_shape':list(inputs['pixel_values'].shape)}, 'generated_tokens':new.shape[1],'elapsed_seconds':round(time.monotonic()-start,3),
            'peak_rss_mib':round(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss/1024,1),
            'external_ai_requests':0,'external_ai_cost':0,'intersection_verified':False})
        Path(job['output']).write_text(json.dumps(receipt,ensure_ascii=False,indent=2))


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--worker',required=True)
    worker(parser.parse_args().worker)
