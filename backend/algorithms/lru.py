"""
Least Recently Used (LRU) Page Replacement Algorithm.
Independent baseline implementation for comparison against ARC.

This module is completely independent of FastAPI and GUI frameworks.
"""
from collections import OrderedDict
from typing import Any, Dict, List, Optional, Tuple


class LRUCache:
    """
    Least Recently Used (LRU) Cache
    
    Replaces the page that has not been accessed for the longest time
    whenever a page fault occurs and the cache is full.
    
    Data Structure Convention:
      - Uses collections.OrderedDict to maintain access recency in O(1) time.
      - End (last=True) is Most Recently Used (MRU).
      - Beginning (last=False) is Least Recently Used (LRU).
      - Membership checks, promotions, and evictions all operate in O(1) time.
    """

    def __init__(self, capacity: int):
        """
        Initialize LRU Cache.
        
        Args:
            capacity: Maximum number of pages the cache can hold (integer >= 1).
        """
        if not isinstance(capacity, int) or capacity < 1:
            raise ValueError(f"Cache capacity must be a positive integer >= 1, got {capacity}")
        
        self.capacity: int = capacity
        
        # OrderedDict storing resident pages: page_key -> None
        # Head (last=False) is LRU, Tail (last=True) is MRU
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
        Process a page reference request.
        
        On a hit:
          - Increment hit counter.
          - Move page to MRU position (last=True).
        On a miss:
          - Increment miss counter.
          - If full, evict LRU page (last=False) and increment replacements.
          - Insert page at MRU position.
          
        Args:
            page: Identifier of the requested page (integer, string, etc.).
            
        Returns:
            Telemetry dictionary describing the access event and post-request state.
        """
        self.requests += 1
        evicted_page = None
        
        if page in self.cache:
            # Page Hit
            self.hits += 1
            hit = True
            hit_type = "HIT"
            # Promote to MRU (tail)
            self.cache.move_to_end(page, last=True)
            movement = f"Page {page} hit; promoted to MRU"
        else:
            # Page Miss (Fault)
            self.misses += 1
            hit = False
            hit_type = "MISS"
            
            if len(self.cache) >= self.capacity:
                # Evict Least Recently Used page (head)
                evicted_page, _ = self.cache.popitem(last=False)
                self.replacements += 1
                movement = f"Cache full; evicted LRU page {evicted_page}; inserted {page} at MRU"
            else:
                movement = f"Cache warmup; inserted {page} at MRU"
                
            # Insert at MRU (tail)
            self.cache[page] = None
            self.cache.move_to_end(page, last=True)
            
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
        Capture current snapshot of the LRU cache state.
        
        Returns:
            Dictionary containing cache capacity, resident pages in MRU-to-LRU order,
            explicit LRU and MRU indicators, and performance counters.
        """
        keys_lru_to_mru = list(self.cache.keys())
        keys_mru_to_lru = list(reversed(keys_lru_to_mru))
        
        return {
            "capacity": self.capacity,
            "size": len(self.cache),
            "size_cache": len(self.cache),
            "pages": keys_mru_to_lru,
            "mru_to_lru": keys_mru_to_lru,
            "lru_to_mru": keys_lru_to_mru,
            "mru": keys_mru_to_lru[0] if keys_mru_to_lru else None,
            "lru": keys_lru_to_mru[0] if keys_lru_to_mru else None,
            "requests": self.requests,
            "hits": self.hits,
            "misses": self.misses,
            "hit_ratio": self.hit_ratio,
            "replacements": self.replacements,
            "order": "MRU_to_LRU"
        }

    def reset(self) -> None:
        """Reset the cache and all counters to pristine initial state."""
        self.cache.clear()
        self.requests = 0
        self.hits = 0
        self.misses = 0
        self.replacements = 0

