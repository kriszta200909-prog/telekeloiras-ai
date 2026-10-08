"""Local OCR process: expensive native OCR must not block Streamlit's GIL."""
import base64
import json
import sys

import fitz


def main():
    for line in sys.stdin:
        try:
            request=json.loads(line)
            pix=fitz.Pixmap(base64.b64decode(request['png'],validate=True))
            pix.set_dpi(150,150)
            with fitz.open(stream=pix.pdfocr_tobytes(language='eng',tessdata=request['tessdata']),filetype='pdf') as doc:
                words=[(w[4],(w[0]+w[2])/2*150/72,(w[1]+w[3])/2*150/72)
                       for w in doc[0].get_text('words')]
            result={'words':words}
        except Exception as exc:
            result={'error':str(exc)}
        print(json.dumps(result),flush=True)


if __name__=='__main__':main()
