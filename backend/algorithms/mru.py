"""
Most Recently Used (MRU) Page Replacement Algorithm.
Independent baseline implementation for comparative analysis.

Replaces the page that was accessed most recently when a page fault occurs
and the cache is full. Useful in cyclic access patterns and looping workloads
where the most recently used page is least likely to be needed immediately.

This module is completely independent of FastAPI and GUI frameworks.
"""
from collections import OrderedDict
from typing import Any, Callable, Dict, List, Optional


class MRUCache:
    """
    Most Recently Used (MRU) Cache
    
    Replaces the page with the highest recency (most recently accessed or inserted)
    whenever a page fault occurs and the cache is full.
    
    Data Structure Convention:
      - Uses collections.OrderedDict to maintain access recency in O(1) time.
      - Head (last=False) is Least Recently Used (LRU).
      - Tail (last=True) is Most Recently Used (MRU) - Candidate for eviction!
      - Hits promote accessed page to MRU (tail).
      - Evictions pop from MRU (tail, last=True) in O(1) time.
    """

    def __init__(self, capacity: int):
        """
        Initialize MRU Cache.
        
        Args:
            capacity: Maximum number of pages the cache can hold (integer >= 1).
        """
        if not isinstance(capacity, int) or capacity < 1:
            raise ValueError(f"Cache capacity must be a positive integer >= 1, got {capacity}")
            
        self.capacity: int = capacity
        
        # OrderedDict storing resident pages: page_key -> None
        # Head (last=False) is LRU, Tail (last=True) is MRU
        self.cache: OrderedDict[Any, None] = OrderedDict()
        
        # Performance counters
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

    def contains(self, page: Any) -> bool:
        """Check if page is currently resident in cache."""
        return page in self.cache

    def is_full(self) -> bool:
        """Check if resident cache has reached maximum capacity."""
        return len(self.cache) >= self.capacity

    def request(self, page: Any) -> Dict[str, Any]:
        """
        Process a page reference request according to the MRU policy.
        
        On a hit:
          - Increment hit counter.
          - Move page to MRU position (tail, last=True).
        On a miss:
          - Increment miss counter.
          - If full, evict the Most Recently Used page (tail, last=True).
          - Insert incoming page at MRU position.
          
        Args:
            page: Identifier of the requested page.
            
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
            # Update to MRU (tail)
            self.cache.move_to_end(page, last=True)
            movement = f"Page {page} hit; updated to MRU"
        else:
            # Page Miss (Fault)
            self.misses += 1
            hit = False
            hit_type = "MISS"
            
            if len(self.cache) >= self.capacity:
                # Evict Most Recently Used page (tail)
                evicted_page, _ = self.cache.popitem(last=True)
                self.replacements += 1
                movement = f"Cache full; evicted MRU page {evicted_page}; inserted {page} at MRU"
            else:
                movement = f"Cache warmup; inserted {page} at MRU"
                
            # Insert incoming page at MRU (tail)
            self.cache[page] = None
            self.cache.move_to_end(page, last=True)
            
        # Verify invariant
        assert len(self.cache) <= self.capacity, (
            f"Invariant Violated: MRU cache size {len(self.cache)} exceeds capacity {self.capacity}"
        )
        
        return {
            "page": page,
            "hit": hit,
            "hit_type": hit_type,
            "evicted_page": evicted_page,
            "movement": movement,
            "state": self.get_state()
        }

    # -------------------------------------------------------------------------
    # Decoupled Policy Interface Contract (for OS Memory Manager Integration)
    # -------------------------------------------------------------------------

    def prepare_request(self, incoming_page: Any, process_context: Optional[int] = None) -> Dict[str, Any]:
        """Classify incoming request without mutating cache state."""
        is_hit = incoming_page in self.cache
        return {
            "status": "HIT" if is_hit else "COLD_MISS",
            "target_list": "RESIDENT",
            "requires_eviction": False,
            "eviction_reason": "NONE",
            "evict_mode": "NONE",
            "ghost_evicted": None,
            "delta": 0.0,
            "adapted_p": 0.0
        }

    def select_victim(
        self,
        incoming_page: Any = None,
        process_context: Optional[int] = None,
        is_eligible_cb: Optional[Callable[[Any], bool]] = None
    ) -> Any:
        """
        Identify the candidate victim page without mutating resident state.
        Selects the Most Recently Used (MRU) page at the tail of the cache that satisfies is_eligible_cb.
        """
        if not self.cache:
            return None
        if is_eligible_cb is None:
            return next(reversed(self.cache))
        for page in reversed(self.cache):
            if is_eligible_cb(page):
                return page
        return None

    def purge_pid(self, pid: int) -> int:
        """
        Purge all pages belonging to the specified PID from the cache.
        Non-process keys without a .pid attribute are safely ignored.
        
        Args:
            pid: Process ID whose pages should be purged.
            
        Returns:
            Count of resident pages purged.
        """
        keys_to_purge = [k for k in self.cache if hasattr(k, "pid") and k.pid == pid]
        for k in keys_to_purge:
            del self.cache[k]
        return len(keys_to_purge)

    def evict_victim(self, victim: Any) -> None:
        """Confirm and execute eviction of the specified victim page."""
        if victim in self.cache:
            del self.cache[victim]
            self.replacements += 1

    def insert_page(self, incoming_page: Any, is_write: bool = False) -> None:
        """Insert incoming page at MRU position."""
        if incoming_page in self.cache:
            self.cache.move_to_end(incoming_page, last=True)
        else:
            self.cache[incoming_page] = None
            self.cache.move_to_end(incoming_page, last=True)

    def get_state(self) -> Dict[str, Any]:
        """Capture current snapshot of the MRU cache state."""
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
            "order": "MRU_to_LRU",
            "algorithm": "MRU"
        }

    def reset(self) -> None:
        """Reset the cache and all counters to pristine initial state."""
        self.cache.clear()
        self.requests = 0
        self.hits = 0
        self.misses = 0
        self.replacements = 0

