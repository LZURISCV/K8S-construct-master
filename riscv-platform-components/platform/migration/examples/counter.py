#!/usr/bin/env python3
"""Both values exist only in RAM; a fresh process cannot reproduce them."""
import json
import time
import uuid

boot_id = str(uuid.uuid4())
counter = 0
while True:
    counter += 1
    print(json.dumps({"boot_id": boot_id, "counter": counter, "time_ns": time.time_ns()}), flush=True)
    time.sleep(1)
