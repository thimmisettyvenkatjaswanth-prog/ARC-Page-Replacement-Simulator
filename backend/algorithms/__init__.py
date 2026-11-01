"""
Core Page Replacement Algorithms Package
Contains independent implementations of ARC, LRU, and FIFO without framework dependencies.
"""
from backend.algorithms.arc import ARCCache
from backend.algorithms.fifo import FIFOCache
from backend.algorithms.lru import LRUCache

__all__ = ["ARCCache", "LRUCache", "FIFOCache"]
