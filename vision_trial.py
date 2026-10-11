"""Source-bound image preparation. Historical paid execution is disabled."""
import argparse
import hashlib
import io
import json
import os
from pathlib import Path

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
    manifest = {'paid_execution_disabled':True, 'status': 'prepared_not_run', 'external_ai_requests': 0, 'model': MODEL,
        'credential_present': bool(os.environ.get('OPENAI_API_KEY')), 'api_access_verified': False,
        'source_report_sha256': digest(json.dumps(report, sort_keys=True).encode()), 'cases': entries,
        'total_usd_estimate_upper': round(sum(e['cost']['usd_estimate_upper'] for e in entries), 6)}
    (destination/'manifest.json').write_text(json.dumps(manifest, ensure_ascii=False, indent=2))
    return manifest


def run(folder, consent, max_usd):
    """Historical paid trial is disabled, regardless of flags or credentials."""
    raise ValueError('Fizetős API letiltva. Használd a felület ingyenes helyi modelljét.')


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
