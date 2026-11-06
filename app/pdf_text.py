"""Isolated, resource-bounded PDF text worker. Never performs OCR or network IO."""
from __future__ import annotations
import io,json,sys


def main():
    import resource
    resource.setrlimit(resource.RLIMIT_CPU, (15, 15))
    resource.setrlimit(resource.RLIMIT_AS, (512 * 1024 * 1024, 512 * 1024 * 1024))
    from pypdf import PdfReader
    data = sys.stdin.buffer.read(12_000_001)
    if len(data) > 12_000_000 or not data.startswith(b'%PDF-'):
        raise ValueError('Unsupported PDF')
    reader = PdfReader(io.BytesIO(data))
    pages = []
    size = 0
    for page in reader.pages[:30]:
        contents = page.get_contents()
        if contents is not None and len(contents.get_data()) > 4_000_000:
            break
        text = page.extract_text() or ''
        text = text[:20000]
        pages.append(text)
        size += len(text)
        if size >= 160000:
            break
    print(json.dumps({'text': '\n\n'.join(pages)[:160000]}))


if __name__ == '__main__':
    main()
