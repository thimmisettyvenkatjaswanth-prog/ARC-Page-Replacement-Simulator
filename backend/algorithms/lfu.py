"""
Least Frequently Used (LFU) Page Replacement Algorithm.
Independent baseline implementation for comparative analysis.

Replaces the page with the lowest access frequency when a page fault occurs
and the cache is full. Ties in frequency are broken deterministically using
Least Recently Used (LRU) among pages with equal frequency.

This module is completely independent of FastAPI and GUI frameworks.
"""
from collections import OrderedDict
from typing import Any, Dict, List, Optional


class LFUCache:
    """
    Least Frequently Used (LFU) Cache with LRU tie-breaking.
    
    Data Structure Convention:
      - page_to_freq: Dict[page_key, int] tracks current access frequency.
      - freq_to_pages: Dict[freq, OrderedDict[page_key, None]] maintains pages
        at each frequency level in LRU-to-MRU recency order (head=LRU, tail=MRU).
      - min_freq: int pointer to the current minimum frequency in resident cache.
      - Lookup, hit promotion, and eviction all operate in O(1) amortized time.
    """

    def __init__(self, capacity: int):
        """
        Initialize LFU Cache.
        
        Args:
            capacity: Maximum number of pages the cache can hold (integer >= 1).
        """
        if not isinstance(capacity, int) or capacity < 1:
            raise ValueError(f"Cache capacity must be a positive integer >= 1, got {capacity}")
            
        self.capacity: int = capacity
        
        # Mapping from page identifier to its access frequency
        self.page_to_freq: Dict[Any, int] = {}
        
        # Mapping from frequency level to OrderedDict of pages (LRU-to-MRU)
        self.freq_to_pages: Dict[int, OrderedDict[Any, None]] = {}
        
        # Pointer to the current minimum frequency in resident set
        self.min_freq: int = 0
        
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
        return page in self.page_to_freq

    def is_full(self) -> bool:
        """Check if resident cache has reached maximum capacity."""
        return len(self.page_to_freq) >= self.capacity

    def request(self, page: Any) -> Dict[str, Any]:
        """
        Process a page reference request according to the LFU policy with LRU tie-breaking.
        
        On a hit:
          - Increment hit counter.
          - Increment frequency count of the page.
          - Move page from old frequency bucket to the tail of (freq + 1) bucket.
          - If the old frequency bucket was min_freq and is now empty, advance min_freq.
        On a miss:
          - Increment miss counter.
          - If full, evict the LRU page from min_freq bucket and increment replacements.
          - Insert incoming page with frequency 1 and set min_freq to 1.
          
        Args:
            page: Identifier of the requested page.
            
        Returns:
            Telemetry dictionary describing the access event and post-request state.
        """
        self.requests += 1
        evicted_page = None
        
        if page in self.page_to_freq:
            # Page Hit
            self.hits += 1
            hit = True
            hit_type = "HIT"
            
            old_freq = self.page_to_freq[page]
            new_freq = old_freq + 1
            self.page_to_freq[page] = new_freq
            
            # Remove from old frequency bucket
            del self.freq_to_pages[old_freq][page]
            if not self.freq_to_pages[old_freq]:
                del self.freq_to_pages[old_freq]
                if self.min_freq == old_freq:
                    self.min_freq = new_freq
                    
            # Insert into new frequency bucket at tail (MRU for this freq)
            self.freq_to_pages.setdefault(new_freq, OrderedDict())[page] = None
            movement = f"Page {page} hit; frequency incremented from {old_freq} to {new_freq}"
        else:
            # Page Miss (Fault)
            self.misses += 1
            hit = False
            hit_type = "MISS"
            
            if len(self.page_to_freq) >= self.capacity:
                # Evict LRU page from minimum frequency bucket
                evicted_page, _ = self.freq_to_pages[self.min_freq].popitem(last=False)
                del self.page_to_freq[evicted_page]
                if not self.freq_to_pages[self.min_freq]:
                    del self.freq_to_pages[self.min_freq]
                self.replacements += 1
                movement = (
                    f"Cache full; evicted LFU page {evicted_page} (frequency {self.min_freq}); "
                    f"inserted {page} with frequency 1"
                )
            else:
                movement = f"Cache warmup; inserted {page} with frequency 1"
                
            # Insert new page at frequency 1
            self.page_to_freq[page] = 1
            self.freq_to_pages.setdefault(1, OrderedDict())[page] = None
            self.min_freq = 1
            
        # Verify invariant
        assert len(self.page_to_freq) <= self.capacity, (
            f"Invariant Violated: LFU cache size {len(self.page_to_freq)} exceeds capacity {self.capacity}"
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
        is_hit = incoming_page in self.page_to_freq
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

    def select_victim(self, incoming_page: Any = None, process_context: Optional[int] = None) -> Any:
        """
        Identify the candidate victim page without mutating resident state.
        Selects the LRU page from the current minimum frequency bucket.
        """
        if not self.page_to_freq:
            return None
        min_bucket = self.freq_to_pages.get(self.min_freq)
        if not min_bucket:
            # Fallback to finding minimum key if pointer desynchronized
            self.min_freq = min(self.freq_to_pages.keys())
            min_bucket = self.freq_to_pages[self.min_freq]
        return next(iter(min_bucket))

    def evict_victim(self, victim: Any) -> None:
        """Confirm and execute eviction of the specified victim page."""
        if victim in self.page_to_freq:
            freq = self.page_to_freq[victim]
            if freq in self.freq_to_pages and victim in self.freq_to_pages[freq]:
                del self.freq_to_pages[freq][victim]
                if not self.freq_to_pages[freq]:
                    del self.freq_to_pages[freq]
            del self.page_to_freq[victim]
            self.replacements += 1
            if self.page_to_freq and (self.min_freq not in self.freq_to_pages):
                self.min_freq = min(self.freq_to_pages.keys())

    def insert_page(self, incoming_page: Any, is_write: bool = False) -> None:
        """Insert incoming page into resident state with initial frequency 1."""
        if incoming_page in self.page_to_freq:
            # Page already present, promote frequency
            old_freq = self.page_to_freq[incoming_page]
            new_freq = old_freq + 1
            self.page_to_freq[incoming_page] = new_freq
            del self.freq_to_pages[old_freq][incoming_page]
            if not self.freq_to_pages[old_freq]:
                del self.freq_to_pages[old_freq]
                if self.min_freq == old_freq:
                    self.min_freq = new_freq
            self.freq_to_pages.setdefault(new_freq, OrderedDict())[incoming_page] = None
        else:
            self.page_to_freq[incoming_page] = 1
            self.freq_to_pages.setdefault(1, OrderedDict())[incoming_page] = None
            self.min_freq = 1

    def get_state(self) -> Dict[str, Any]:
        """Capture current snapshot of the LFU cache state."""
        resident_pages = list(self.page_to_freq.keys())
        return {
            "capacity": self.capacity,
            "size": len(self.page_to_freq),
            "size_cache": len(self.page_to_freq),
            "pages": resident_pages,
            "frequencies": dict(self.page_to_freq),
            "min_frequency": self.min_freq if self.page_to_freq else None,
            "requests": self.requests,
            "hits": self.hits,
            "misses": self.misses,
            "hit_ratio": self.hit_ratio,
            "replacements": self.replacements,
            "algorithm": "LFU"
        }

    def reset(self) -> None:
        """Reset the cache and all counters to pristine initial state."""
        self.page_to_freq.clear()
        self.freq_to_pages.clear()
        self.min_freq = 0
        self.requests = 0
        self.hits = 0
        self.misses = 0
        self.replacements = 0

