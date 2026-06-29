"""Run the OS process enumeration in a STANDALONE process and emit JSON.

Invoked as a subprocess by the radar scanner (`scan_all_safe`). macOS can wedge
process enumeration (`psutil.process_iter`) inside a stuck syscall that never
releases the GIL — which would freeze the whole gateway process, so uvicorn could
never bind :8000 (only a reboot cleared it). Running the scan HERE, in a child
process, lets the parent kill it on a timeout and stay healthy.

Contract: print a JSON list of AgentEntry dumps to stdout and exit 0. On any
failure, print "[]" so the parent always receives valid JSON (a skipped cycle),
never a traceback on stdout. Run as: ``python -m awcp.radar.detectors``.
"""

from __future__ import annotations

import json
import sys


def main() -> int:
    try:
        from awcp.radar.detectors import scan_all

        entries = [e.model_dump(mode="json") for e in scan_all()]
        sys.stdout.write(json.dumps(entries))
    except Exception as exc:  # noqa: BLE001 — emit empty result, never a traceback to stdout
        sys.stderr.write(f"radar.scan.subprocess error: {exc!r}\n")
        sys.stdout.write("[]")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
