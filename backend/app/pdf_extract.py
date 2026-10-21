"""Resource-limited PDF parsing child; parent enforces a wall-clock deadline."""

import io
import logging
import resource
import sys

resource.setrlimit(resource.RLIMIT_CPU, (5, 5))
if sys.platform.startswith("linux"):
    resource.setrlimit(resource.RLIMIT_AS, (768 * 1024 * 1024,) * 2)


def main():
    from pypdf import PdfReader

    logging.disable(logging.CRITICAL)
    content = sys.stdin.buffer.read(20 * 1024 * 1024 + 1)
    if len(content) > 20 * 1024 * 1024:
        raise ValueError("pdf_size")
    reader = PdfReader(io.BytesIO(content))
    if reader.is_encrypted or len(reader.pages) > 50:
        raise ValueError("pdf_limit")
    parts = []
    for page in reader.pages:
        stream = page.get_contents()
        if stream and len(stream.get_data()) > 8 * 1024 * 1024:
            raise ValueError("pdf_content_limit")
        parts.append((page.extract_text() or "")[:6000])
        if sum(map(len, parts)) >= 30000:
            break
    sys.stdout.write("\n".join(parts)[:30000])


if __name__ == "__main__":
    main()
