"""
Core Page Replacement Algorithms Package.

Contains independent, standalone implementations of:
- ARC: Adaptive Replacement Cache (Megiddo & Modha, FAST 2003)
- LRU: Least Recently Used
- FIFO: First-In First-Out
- LFU: Least Frequently Used (with deterministic LRU tie-breaking)
- MRU: Most Recently Used
- CLOCK: Second-Chance circular buffer algorithm
- OPT: Belady's Optimal offline clairvoyant baseline

All algorithms operate independently without FastAPI or GUI dependencies.
"""
from backend.algorithms.arc import ARCCache
from backend.algorithms.clock import CLOCKCache
from backend.algorithms.fifo import FIFOCache
from backend.algorithms.lfu import LFUCache
from backend.algorithms.lru import LRUCache
from backend.algorithms.mru import MRUCache
from backend.algorithms.opt import OPTCache, OptimalCache

__all__ = [
    "ARCCache",
    "LRUCache",
    "FIFOCache",
    "LFUCache",
    "MRUCache",
    "CLOCKCache",
    "OptimalCache",
    "OPTCache",
]
