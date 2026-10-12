"""Isolated, resource-bounded validation; original bytes are never rewritten."""
import base64
import io
import json
import logging
import resource
import sys
import warnings


def validate(data):
    from PIL import Image, ImageOps
    from pypdf import PdfReader
    logging.getLogger('pypdf').disabled=True
    if data.startswith(b'%PDF-'):
        reader=PdfReader(io.BytesIO(data),strict=True)
        if reader.is_encrypted or not 1<=len(reader.pages)<=100:
            raise ValueError('Use an unencrypted PDF with 1–100 pages.')
        seen=set()
        def inspect(obj, depth=0):
            if depth>80 or len(seen)>10000:
                raise ValueError('PDF structure is too complex.')
            if hasattr(obj,'idnum'):
                marker=(obj.idnum,obj.generation)
                if marker in seen:
                    return
                seen.add(marker)
                obj=obj.get_object()
            if isinstance(obj,dict):
                if set(obj)&{'/JS','/JavaScript','/EmbeddedFiles','/EF','/AF','/AcroForm','/AA','/XFA','/RichMedia'} or obj.get('/S') in ('/Launch','/JavaScript','/SubmitForm','/ImportData','/GoToR','/GoToE','/Rendition','/Sound','/Movie'):
                    raise ValueError('Use a static PDF without scripts, embedded files or interactive forms.')
                for child in obj.values():
                    inspect(child,depth+1)
            elif isinstance(obj,(list,tuple)):
                for child in obj:
                    inspect(child,depth+1)
        inspect(reader.trailer)
        return dict(mime='application/pdf',suffix='pdf',pages=len(reader.pages),preview=None)
    Image.MAX_IMAGE_PIXELS=30_000_000
    with warnings.catch_warnings():
        warnings.simplefilter('error',Image.DecompressionBombWarning)
        with Image.open(io.BytesIO(data)) as image:
            formats={'JPEG':('image/jpeg','jpg'),'PNG':('image/png','png'),'WEBP':('image/webp','webp')}
            if image.format not in formats or getattr(image,'n_frames',1)!=1:
                raise ValueError('Use a single JPEG, PNG or WebP image, or PDF.')
            mime,suffix=formats[image.format]
            image.verify()
        with Image.open(io.BytesIO(data)) as image:
            image.load()
            image=ImageOps.exif_transpose(image)
            image.thumbnail((1600,1600))
            rgba=image.convert('RGBA')
            clean=Image.new('RGB',rgba.size,'white')
            clean.paste(rgba,mask=rgba.getchannel('A'))
            preview=io.BytesIO()
            clean.save(preview,format='JPEG',quality=85)
        return dict(mime=mime,suffix=suffix,pages=None,preview=base64.b64encode(preview.getvalue()).decode())


if __name__=='__main__':
    resource.setrlimit(resource.RLIMIT_AS,(512*1024*1024,512*1024*1024))
    resource.setrlimit(resource.RLIMIT_CPU,(10,10))
    try:
        data=sys.stdin.buffer.read(10485761)
        if not 1<=len(data)<=10485760:
            raise ValueError('Choose a file up to 10 MiB.')
        print(json.dumps(validate(data)))
    except Exception:
        # Parser diagnostics can contain document text; never echo them.
        print(json.dumps({'error':'Use a valid, static JPEG, PNG, WebP or unencrypted PDF (up to 100 pages / 30 megapixels).'}))
