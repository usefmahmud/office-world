import os
import tempfile

# Keep tests hermetic: no writes into the repo, no background ticking.
os.environ.setdefault("WORLD_STATE_DIR", tempfile.mkdtemp(prefix="world-test-"))
os.environ.setdefault("WORLD_ALLOW_MANUAL_TICK", "1")
os.environ.setdefault("TICK", "3600")
os.environ.setdefault("WORLD_SEED", "7")
