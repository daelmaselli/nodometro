"""Report stalled publication to Compose without modifying metric snapshots."""
from pathlib import Path
import sys
import time

LIMITS = {
    "image-check": {"images": 90},
    "docker-prune": {"prune": 120},
    "zfs": {"pools": 120, "datasets": 360},
}

try:
    healthy = all(time.time() - (Path("/metrics") / (name + ".prom")).stat().st_mtime <= age
                  for name, age in LIMITS[sys.argv[1]].items())
except OSError:
    healthy = False
sys.exit(0 if healthy else 1)
