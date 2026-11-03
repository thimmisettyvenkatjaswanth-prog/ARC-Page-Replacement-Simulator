"""
Optimal (OPT / Belady's MIN) Page Replacement Algorithm.
Offline Clairvoyant Baseline Implementation for Comparative Analysis.

Belady's Optimal algorithm replaces the page that will not be used for the
longest period of time in the future. If a page will never be referenced again,
its future distance is infinity and it is selected as the first candidate.
Ties in future distance (e.g., multiple pages never referenced again) are
broken deterministically using FIFO arrival order.

OPT is provably optimal (minimizes total page faults for any given reference
string under fixed capacity), but is physically unrealizable in online operating
systems because it requires complete knowledge of all future memory accesses.
It serves as the theoretical upper bound / offline benchmark against which
online algorithms (ARC, LRU, LFU, FIFO, CLOCK) are evaluated.

This module is completely independent of FastAPI and GUI frameworks.
"""
from collections import OrderedDict, deque
from typing import Any, Dict, List, Optional, Sequence


class OptimalCache:
    """
    Belady's Optimal (OPT / MIN) Cache Implementation.
    
    Offline Clairvoyant Policy:
      - Requires the full future reference sequence prior to simulation.
      - Precomputes future occurrence index queues per distinct page.
      - Evicts the resident page whose next occurrence index is furthest in the future.
    """

    def __init__(self, capacity: int, reference_sequence: Optional[Sequence[Any]] = None):
        """
        Initialize Optimal Cache.
        
        Args:
            capacity: Maximum number of pages the cache can hold (integer >= 1).
            reference_sequence: Optional full future sequence of page references.
        """
        if not isinstance(capacity, int) or capacity < 1:
            raise ValueError(f"Cache capacity must be a positive integer >= 1, got {capacity}")
            
        self.capacity: int = capacity
        
        # Resident pages tracked in OrderedDict for deterministic FIFO tie-breaking: page -> arrival_step
        self.cache: OrderedDict[Any, int] = OrderedDict()
        
        # Stored reference sequence and future lookup index
        self.sequence: List[Any] = []
        self.page_to_future_indices: Dict[Any, deque[int]] = {}
        
        # Step counter tracking progress through the sequence
        self.current_step: int = 0
        
        # Performance counters
        self.requests: int = 0
        self.hits: int = 0
        self.misses: int = 0
        self.replacements: int = 0
        
        if reference_sequence is not None:
            self.initialize_sequence(reference_sequence)

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

    def initialize_sequence(self, reference_sequence: Sequence[Any]) -> None:
        """
        Precompute future occurrence index queues for the entire reference string.
        
        Args:
            reference_sequence: Iterable of page identifiers representing the full workload trace.
        """
        self.sequence = list(reference_sequence)
        self.page_to_future_indices.clear()
        
        for idx, page in enumerate(self.sequence):
            if page not in self.page_to_future_indices:
                self.page_to_future_indices[page] = deque()
            self.page_to_future_indices[page].append(idx)
            
        self.current_step = 0

    def initialize_future_sequence(self, reference_sequence: Sequence[Any]) -> None:
        """Alias for initialize_sequence to conform with architectural specification."""
        self.initialize_sequence(reference_sequence)

    def _future_distance(self, page: Any) -> float:
        """Calculate distance to the next future reference of a page."""
        q = self.page_to_future_indices.get(page)
        if q and len(q) > 0:
            return float(q[0] - self.current_step)
        return float('inf')

    def request(self, page: Any) -> Dict[str, Any]:
        """
        Process a page reference request according to Belady's Optimal policy.
        
        Args:
            page: Identifier of the requested page.
            
        Returns:
            Telemetry dictionary describing the access event and post-request state.
            
        Raises:
            ValueError: If the future reference sequence was never initialized.
        """
        if not self.sequence:
            raise ValueError(
                "OPT is an offline algorithm requiring the full future reference sequence. "
                "Call initialize_sequence() or pass reference_sequence to constructor before request()."
            )
            
        if self.current_step >= len(self.sequence):
            raise ValueError(
                f"Reference sequence exhausted: request at step index {self.current_step} "
                f"exceeds sequence length {len(self.sequence)}. "
                f"OPT cannot process requests beyond the initialized future sequence."
            )

        expected_page = self.sequence[self.current_step]
        if page != expected_page:
            raise ValueError(
                f"Sequence mismatch at index {self.current_step}: "
                f"expected page {expected_page!r}, received page {page!r}. "
                f"OPT requires requests to strictly match the initialized reference sequence."
            )

        self.requests += 1
        evicted_page = None
        
        # Consume the current step from the page's future occurrences deque
        if page in self.page_to_future_indices and self.page_to_future_indices[page]:
            if self.page_to_future_indices[page][0] == self.current_step:
                self.page_to_future_indices[page].popleft()
                
        if page in self.cache:
            # Page Hit
            self.hits += 1
            hit = True
            hit_type = "HIT"
            movement = f"Page {page} hit; resident in OPT cache"
        else:
            # Page Miss (Fault)
            self.misses += 1
            hit = False
            hit_type = "MISS"
            
            if len(self.cache) >= self.capacity:
                # Evict resident page with the furthest next reference (or infinity)
                # Ties broken by earliest arrival order in OrderedDict (FIFO)
                victim = None
                max_dist = -1.0
                
                for res_page in self.cache:
                    dist = self._future_distance(res_page)
                    if dist > max_dist:
                        max_dist = dist
                        victim = res_page
                        
                del self.cache[victim]
                evicted_page = victim
                self.replacements += 1
                
                dist_str = "infinity (never used again)" if max_dist == float('inf') else f"{int(max_dist)} steps"
                movement = (
                    f"Cache full; evicted OPT victim {victim} (next use in {dist_str}); "
                    f"inserted {page}"
                )
            else:
                movement = f"Cache warmup; inserted {page}"
                
            self.cache[page] = self.current_step
            
        self.current_step += 1
        
        # Verify invariant
        assert len(self.cache) <= self.capacity, (
            f"Invariant Violated: OPT cache size {len(self.cache)} exceeds capacity {self.capacity}"
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
        if self.sequence:
            if self.current_step >= len(self.sequence):
                raise ValueError(
                    f"Reference sequence exhausted: request at step index {self.current_step} "
                    f"exceeds sequence length {len(self.sequence)}. "
                    f"OPT cannot process requests beyond the initialized future sequence."
                )
            expected_page = self.sequence[self.current_step]
            if incoming_page != expected_page:
                raise ValueError(
                    f"Sequence mismatch at index {self.current_step}: "
                    f"expected page {expected_page!r}, received page {incoming_page!r}. "
                    f"OPT requires requests to strictly match the initialized reference sequence."
                )

        # In decoupled lifecycle, consume current step for incoming_page if pending
        if incoming_page in self.page_to_future_indices and self.page_to_future_indices[incoming_page]:
            if self.page_to_future_indices[incoming_page][0] == self.current_step:
                self.page_to_future_indices[incoming_page].popleft()

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

    def select_victim(self, incoming_page: Any = None, process_context: Optional[int] = None) -> Any:
        """
        Identify the candidate victim page with the furthest future reference distance.
        Does not mutate resident cache.
        """
        if not self.cache or len(self.cache) < self.capacity:
            return None
            
        victim = None
        max_dist = -1.0
        
        for res_page in self.cache:
            dist = self._future_distance(res_page)
            if dist > max_dist:
                max_dist = dist
                victim = res_page
                
        return victim

    def evict_victim(self, victim: Any) -> None:
        """Confirm and execute eviction of the specified victim page."""
        if victim in self.cache:
            del self.cache[victim]
            self.replacements += 1

    def insert_page(self, incoming_page: Any, is_write: bool = False) -> None:
        """Insert incoming page into resident state and advance step counter in decoupled lifecycle."""
        self.cache[incoming_page] = self.current_step
        self.current_step += 1

    def get_state(self) -> Dict[str, Any]:
        """Capture current snapshot of the OPT cache state."""
        resident_pages = list(self.cache.keys())
        distances = {p: self._future_distance(p) for p in resident_pages}
        
        return {
            "capacity": self.capacity,
            "size": len(self.cache),
            "size_cache": len(self.cache),
            "pages": resident_pages,
            "future_distances": {
                p: ("inf" if dist == float('inf') else int(dist))
                for p, dist in distances.items()
            },
            "current_step": self.current_step,
            "sequence_length": len(self.sequence),
            "requests": self.requests,
            "hits": self.hits,
            "misses": self.misses,
            "hit_ratio": self.hit_ratio,
            "replacements": self.replacements,
            "algorithm": "OPT",
            "is_offline": True
        }

    def reset(self) -> None:
        """Reset the cache and all counters to initial state; recomputes future indices."""
        self.cache.clear()
        self.requests = 0
        self.hits = 0
        self.misses = 0
        self.replacements = 0
        if self.sequence:
            self.initialize_sequence(list(self.sequence))
        else:
            self.current_step = 0


# Canonical Aliases
OPTCache = OptimalCache
