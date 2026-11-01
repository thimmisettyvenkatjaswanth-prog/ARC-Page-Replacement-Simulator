"""
First-In, First-Out (FIFO) Page Replacement Algorithm.
Independent baseline implementation for comparison against LRU and ARC.

This module is completely independent of FastAPI and GUI frameworks.
"""
from collections import OrderedDict
from typing import Any, Dict, List, Optional


class FIFOCache:
    """
    First-In, First-Out (FIFO) Cache
    
    Replaces the page that entered memory earliest (oldest arrival),
    regardless of how recently or frequently it was accessed.
    
    Data Structure Convention:
      - Uses collections.OrderedDict maintaining arrival order in O(1) time.
      - Head (last=False) is Oldest (first-in, candidate for eviction).
      - Tail (last=True) is Newest (most recently arrived).
      - On a hit: Hit counter increments; arrival order remains unchanged.
      - On a miss: If full, oldest page (head) is evicted. Incoming page is appended at tail.
      - Lookup, insertion, and eviction all operate in O(1) time.
    """

    def __init__(self, capacity: int):
        """
        Initialize FIFO Cache.
        
        Args:
            capacity: Maximum number of pages the cache can hold (integer >= 1).
        """
        if not isinstance(capacity, int) or capacity < 1:
            raise ValueError(f"Cache capacity must be a positive integer >= 1, got {capacity}")
            
        self.capacity: int = capacity
        
        # OrderedDict storing resident pages in arrival order: page_key -> None
        self.cache: OrderedDict[Any, None] = OrderedDict()
        
        # Counters
        self.requests: int = 0
        self.hits: int = 0
        self.misses: int = 0
        self.replacements: int = 0

    @property
    def hit_ratio(self) -> float:
        """Calculate current cumulative hit ratio."""
        if self.requests == 0:
            return 0.0
        return round(self.hits / self.requests, 4)

    def request(self, page: Any) -> Dict[str, Any]:
        """
        Process a page reference request according to the FIFO policy.
        
        On a hit:
          - Increment hit counter.
          - Maintain existing arrival order (no reordering).
        On a miss:
          - Increment miss counter.
          - If full, evict the page that arrived earliest (head, last=False).
          - Insert the new page at the newest position (tail, last=True).
          
        Args:
            page: Identifier of the requested page.
            
        Returns:
            Telemetry dictionary describing the access event and post-request state.
        """
        self.requests += 1
        evicted_page = None
        
        if page in self.cache:
            # Page Hit: Record hit, but DO NOT modify insertion/arrival order
            self.hits += 1
            hit = True
            hit_type = "HIT"
            movement = f"Page {page} hit; FIFO order unchanged"
        else:
            # Page Miss (Fault)
            self.misses += 1
            hit = False
            hit_type = "MISS"
            
            if len(self.cache) >= self.capacity:
                # Evict oldest resident page (first-in)
                evicted_page, _ = self.cache.popitem(last=False)
                self.replacements += 1
                movement = f"Cache full; evicted oldest page {evicted_page}; inserted {page} at newest"
            else:
                movement = f"Cache warmup; inserted {page} at newest"
                
            # Append new page at newest position (tail)
            self.cache[page] = None
            
        # Verify invariant
        assert len(self.cache) <= self.capacity, (
            f"Invariant Violated: Cache size {len(self.cache)} exceeds capacity {self.capacity}"
        )
        
        return {
            "page": page,
            "hit": hit,
            "hit_type": hit_type,
            "evicted_page": evicted_page,
            "movement": movement,
            "state": self.get_state()
        }

    def get_state(self) -> Dict[str, Any]:
        """
        Capture current snapshot of the FIFO cache state.
        
        Returns:
            Dictionary containing cache capacity, resident pages,
            explicit oldest and newest page indicators, and performance counters.
        """
        keys_oldest_to_newest = list(self.cache.keys())
        keys_newest_to_oldest = list(reversed(keys_oldest_to_newest))
        
        oldest_page = keys_oldest_to_newest[0] if keys_oldest_to_newest else None
        newest_page = keys_newest_to_oldest[0] if keys_newest_to_oldest else None
        
        return {
            "capacity": self.capacity,
            "size": len(self.cache),
            "size_cache": len(self.cache),
            "pages": keys_newest_to_oldest,
            "newest_to_oldest": keys_newest_to_oldest,
            "oldest_to_newest": keys_oldest_to_newest,
            "newest": newest_page,
            "oldest": oldest_page,
            "requests": self.requests,
            "hits": self.hits,
            "misses": self.misses,
            "hit_ratio": self.hit_ratio,
            "replacements": self.replacements,
            "order": "newest_to_oldest"
        }

    def reset(self) -> None:
        """Reset the cache and all counters to pristine initial state."""
        self.cache.clear()
        self.requests = 0
        self.hits = 0
        self.misses = 0
        self.replacements = 0

