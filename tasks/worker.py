"""Worker entry point — run via: python -m tasks.worker or dramatiq tasks.actors."""

from tasks.actors import actor_echo  # noqa: F401
from tasks.broker import broker  # noqa: F401

if __name__ == "__main__":
    import subprocess
    import sys

    sys.exit(subprocess.call(["dramatiq", "tasks.actors"]))
