"""Isolated, blind multimodal trial. Preparation is offline; run requires spend consent."""
import argparse
import base64
import hashlib
import io
import json
import math
import os
from pathlib import Path
import time
import urllib.request
import urllib.error

import fitz
from PIL import Image
from visual_plan import vision_request, add_source_legend

MODEL = 'gpt-4.1-mini-2025-04-14'
PRICING_URL = 'https://developers.openai.com/api/docs/models/gpt-4.1-mini'


def digest(raw):
    return hashlib.sha256(raw).hexdigest()


def source_path(identity, cache):
    path = Path(cache) / (digest(identity['source_url'].encode()) + '.pdf')
    if not path.is_file() or digest(path.read_bytes()) != identity['source_hash']:
        raise ValueError('Missing or changed official source archive: ' + identity['source_url'])
    return path


def estimate(images):
    # 4.1-mini: at most 1536 32px patches/image, multiplier 1.62.
    # Conservative prompt/schema allowance, not a guaranteed billing limit.
    tokens = 4096
    dimensions = []
    for raw in images:
        with Image.open(io.BytesIO(raw)) as image:
            width, height = image.size
        dimensions.append([width, height])
        tokens += 2490  # ceil(1536 * 1.62), avoids undercount after image resizing.
    return {'image_dimensions': dimensions, 'input_token_budget_estimate': tokens,
            'output_token_limit': 1200, 'usd_estimate_upper': round((tokens*.40+1200*1.60)/1e6, 6),
            'input_usd_per_million': .40, 'output_usd_per_million': 1.60,
            'pricing_source': PRICING_URL, 'pricing_checked': '2026-10-10',
            'assumptions': 'Uncached standard API, no tools/retries; 4096 text/schema tokens plus maximum image patches. Estimate, not billing guarantee; taxes/FX excluded.'}



def trial_request(images, hrsz):
    payload = vision_request(images, hrsz)
    schema = payload['text']['format']['schema']
    schema['properties'].update(parcel_identified={'type':'boolean'},
        visual_evidence={'type':'array','items':{'type':'string'}},
        boundary_state={'type':'string','enum':['closed','open','unclear']})
    schema['required'] += ['parcel_identified', 'visual_evidence', 'boundary_state']
    payload['input'][0]['content'][0]['text'] += (
        ' Több jelmagyarázatkép esetén mindet értelmezd. A parcel_identified mezőben jelöld, hogy '
        'a keresett HRSZ a képen ténylegesen azonosítható-e. A visual_evidence listában írd le '
        'a konkrét képi jeleket és helyüket; a boundary_state csak a saját jelmagyarázat szerinti '
        'övezethatár tényleges zártságát jelölheti. A szaggatott/pontozott/folytonos vonalváltozatokat '
        'külön vizsgáld. A legvalószínűbb övezet vizuális vélemény, nem hatályos jogi igazolás.')
    return payload


def prepare(report, destination, cache):
    destination = Path(destination)
    destination.mkdir(parents=True, exist_ok=True)
    entries = []
    # Sequence only: no reference zone answers or parcel coordinates in request.
    for place in ('Miskolc', 'Tiszaújváros', 'Komádi'):
        case = next(c for c in report['cases'] if c['place'] == place)
        visual = case['visual']
        if not (case['plan_inputs'].get('current_verified') and visual.get('legend_bound')
                and visual.get('legend_image_source_verified')):
            raise ValueError('Unverified current source/legend: ' + place)
        identity = case['legend']['identity']
        plan_identity = {'source_url': identity['plan_url'], 'source_hash': identity['plan_hash']}
        with fitz.open(source_path(plan_identity, cache)) as doc:
            page = doc[visual['pdf_page']-1]
            clip = fitz.Rect(visual['clip_pdf'])
            scale = min(5, 1800/max(clip.width, clip.height))
            raw = page.get_pixmap(matrix=fitz.Matrix(scale, scale), clip=clip, alpha=False).tobytes('png')
        if digest(raw) != visual['context_image_sha256']:
            raise ValueError('Source crop differs from verified local control')
        slug = place.lower().replace('ú', 'u').replace('á', 'a')
        folder = destination/slug
        folder.mkdir(exist_ok=True)
        (folder/'plan.png').write_bytes(raw)
        images = [raw]
        files = ['plan.png']
        pages = sorted({r['PDF-oldal'] for r in case['legend']['records']})
        with fitz.open(source_path(identity, cache)) as doc:
            for number in pages:
                page = doc[number-1]
                # Whole legend page: all line variants remain available, no selected answer.
                scale = min(3, 1800/max(page.rect.width, page.rect.height))
                raw = page.get_pixmap(matrix=fitz.Matrix(scale, scale), alpha=False).tobytes('png')
                name = f'legend-{number}.png'
                (folder/name).write_bytes(raw)
                images.append(raw)
                files.append(name)
        excerpts = add_source_legend({'annotated_png': images[0]}, case['legend'], Path(cache))
        if not excerpts.get('legend_image_source_verified') or not excerpts.get('legend_png'):
            raise ValueError('Source legend detail extraction failed')
        (folder/'legend-detail.png').write_bytes(excerpts['legend_png'])
        images.append(excerpts['legend_png'])
        files.append('legend-detail.png')
        payload = trial_request(images, case['hrsz'])
        (folder/'request.json').write_text(json.dumps(payload, ensure_ascii=False, indent=2))
        # Control is separate and NEVER sent to the model.
        (folder/'control.json').write_text(json.dumps({'zone': visual['zone'], 'category': case['classification']['category'],
            'intersection_verified': False, 'candidate_labels': visual['candidate_labels']}, ensure_ascii=False, indent=2))
        entries.append({'place': place, 'hrsz': case['hrsz'], 'folder': slug,
            'plan_source': plan_identity, 'pdf_page': visual['pdf_page'], 'clip_pdf': visual['clip_pdf'],
            'legend_source': identity, 'legend_pages': pages, 'files': files,
            'image_sha256': [digest(i) for i in images], 'request_sha256': digest((folder/'request.json').read_bytes()),
            'cost': estimate(images)})
    manifest = {'status': 'prepared_not_run', 'external_ai_requests': 0, 'model': MODEL,
        'credential_present': bool(os.environ.get('OPENAI_API_KEY')), 'api_access_verified': False,
        'source_report_sha256': digest(json.dumps(report, sort_keys=True).encode()), 'cases': entries,
        'total_usd_estimate_upper': round(sum(e['cost']['usd_estimate_upper'] for e in entries), 6)}
    (destination/'manifest.json').write_text(json.dumps(manifest, ensure_ascii=False, indent=2))
    return manifest


def run(folder, consent, max_usd):
    """One call, no retry/redirect; no claim of legal proof or calibrated confidence."""
    folder = Path(folder)
    manifest = json.loads((folder.parent/'manifest.json').read_text())
    entry = next(e for e in manifest['cases'] if e['folder'] == folder.name)
    if not consent or not math.isfinite(max_usd) or max_usd < entry['cost']['usd_estimate_upper']:
        raise ValueError('Explicit paid-call approval and sufficient estimated budget required')
    if (folder/'result.json').exists():
        raise ValueError('Trial already attempted; preserve its result and prepare a new reviewed output directory')
    if not os.environ.get('OPENAI_API_KEY'):
        raise ValueError('OPENAI_API_KEY unavailable; no external request made')
    raw = (folder/'request.json').read_bytes()
    if digest(raw) != entry['request_sha256']:
        raise ValueError('Prepared request changed; prepare again before approval')
    payload = json.loads(raw)
    if payload['model'] != MODEL or payload.get('store') is not False:
        raise ValueError('Unsupported trial model or storage policy')
    class NoRedirect(urllib.request.HTTPRedirectHandler):
        def redirect_request(self, *args, **kwargs):
            return None
    request = urllib.request.Request('https://api.openai.com/v1/responses', data=raw,
        headers={'Authorization': 'Bearer '+os.environ['OPENAI_API_KEY'], 'Content-Type': 'application/json'})
    started = time.monotonic()
    result = {'status': 'failed', 'external_ai_requests': 1, 'intersection_verified': False,
              'legal_classification_verified': False, 'actual_usd': None, 'model': MODEL,
              'api_access_verified': False}
    try:
        with urllib.request.build_opener(NoRedirect).open(request, timeout=60) as response:
            data = response.read(1024*1024+1)
        if len(data) > 1024*1024:
            raise ValueError('Response too large')
        data = json.loads(data)
        if data.get('status') != 'completed':
            raise ValueError('Incomplete model answer')
        answer = json.loads(''.join(c['text'] for item in data.get('output', []) if item.get('type') == 'message'
            for c in item.get('content', []) if c.get('type') == 'output_text'))
        required = ('zone', 'hrsz', 'boundary_explanation', 'legend_explanation', 'neighbour_zones', 'uncertainties')
        if (not isinstance(answer, dict) or not all(k in answer for k in required)
                or not all(isinstance(answer[k], str) for k in required[:4])
                or not all(isinstance(answer[k], list) and all(isinstance(v, str) for v in answer[k]) for k in required[4:])
                or answer['hrsz'] != entry['hrsz']
                or not isinstance(answer.get('parcel_identified'), bool)
                or answer.get('boundary_state') not in ('closed', 'open', 'unclear')
                or not isinstance(answer.get('visual_evidence'), list)
                or not all(isinstance(v,str) for v in answer['visual_evidence'])):
            raise ValueError('Invalid answer or wrong parcel identifier')
        control = json.loads((folder/'control.json').read_text())
        usage = data.get('usage', {})
        result.update(status='completed', api_access_verified=True, response_id=data.get('id'), answer=answer, usage=usage,
            agrees_with_local_zone=answer['zone'] == control['zone'], local_zone=control['zone'],
            actual_usd=(usage['input_tokens']*.40+usage['output_tokens']*1.60)/1e6 if
                'input_tokens' in usage and 'output_tokens' in usage else None,
            cost_note='Conservative standard-rate calculation; cached-input discount not applied. Not an invoice.')
    except (OSError, ValueError, KeyError, TypeError):
        result['reason'] = 'API/answer failure; no source or legal proof claimed. Cost may still have incurred.'
    result['elapsed_seconds'] = round(time.monotonic()-started, 3)
    (folder/'result.json').write_text(json.dumps(result, ensure_ascii=False, indent=2))
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest='command', required=True)
    p = sub.add_parser('prepare')
    p.add_argument('--report', default='validation/reference-results.json')
    p.add_argument('--output', default='work/vision-trial')
    p.add_argument('--cache', default='/tmp/telekeloiras_pdf_cache')
    p = sub.add_parser('run')
    p.add_argument('folder')
    p.add_argument('--approve-paid-call', action='store_true')
    p.add_argument('--max-estimated-usd', type=float, default=0)
    args = parser.parse_args()
    try:
        result = prepare(json.loads(Path(args.report).read_text()), args.output, args.cache) if args.command == 'prepare' else run(
            args.folder, args.approve_paid_call, args.max_estimated_usd)
    except (ValueError, OSError, StopIteration) as e:
        parser.exit(2, str(e)+'\n')
    print(json.dumps(result, ensure_ascii=False, indent=2))
    if args.command == 'run' and result['status'] != 'completed':
        parser.exit(1)


if __name__ == '__main__':
    main()
