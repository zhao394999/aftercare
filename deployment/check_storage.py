"""One-time cloud storage acceptance; restore the default command afterward."""

import os
import sys

from app.config import settings
from app.storage import discard_file, file_location, store_file

settings().validate_runtime()
payload = b"Aftercare cloud storage verification"
key = store_file("deployment-verification", payload, "text/plain")
try:
    with file_location(key, "verification.txt") as stream:
        assert stream.read() == payload
finally:
    discard_file(key)
print("PRIVATE_STORAGE_ROUND_TRIP_PASSED", flush=True)
os.execv(sys.executable, [sys.executable, "deployment/render_start.py"])
