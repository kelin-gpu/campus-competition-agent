"""Public exports for hackathon source adapters.

The canonical contracts live in :mod:`tools.hackathon_adapters.base`; keeping
one definition avoids candidates from different adapters failing type checks.
"""

from tools.hackathon_adapters.base import BaseAdapter, HackathonCandidate

__all__ = ["BaseAdapter", "HackathonCandidate"]
