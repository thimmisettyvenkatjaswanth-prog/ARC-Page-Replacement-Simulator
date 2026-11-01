"""
Adaptive Replacement Cache (ARC) Algorithm
Faithful implementation of the research paper:
"ARC: A Self-Tuning, Low Overhead Replacement Cache"
by Nimrod Megiddo and Dharmendra S. Modha (USENIX FAST 2003).

This module is completely independent of FastAPI and GUI frameworks.
"""
from collections import OrderedDict
from typing import Any, Dict, List, Optional, Tuple, Union


class ARCCache:
    """
    Adaptive Replacement Cache (ARC)
    
    Partitions cache directory of capacity 2c into:
      - L1 = T1 U B1 (Recency list: pages requested once recently)
          * T1: Resident recent cache pages (holds data / resident pages)
          * B1: Ghost history cache (evicted from T1, page keys only)
      - L2 = T2 U B2 (Frequency list: pages requested >= 2 times recently)
          * T2: Resident frequent cache pages (holds data / resident pages)
          * B2: Ghost history cache (evicted from T2, page keys only)
          
    Adaptive target parameter p in [0, c]:
      - Represents the target size for T1.
      - (c - p) represents the target size for T2.
      - Self-tunes dynamically based on hits in ghost caches B1 and B2.
      
    Data Structure Convention:
      - Each list is an OrderedDict.
      - End (last=True) is Most Recently Used (MRU / top).
      - Beginning (last=False) is Least Recently Used (LRU / bottom).
      - Lookup, insertion, and eviction operate in O(1) time.
    """

    def __init__(self, capacity: int, use_integer_p: bool = False):
        """
        Initialize ARC Cache.
        
        Args:
            capacity: Cache capacity c (positive integer >= 1).
            use_integer_p: 
                - False (default, paper-faithful): Uses exact ratio floating-point arithmetic
                  as published in Megiddo & Modha (USENIX FAST 2003, Figure 4):
                    delta1 = max(1.0, |B2| / |B1|)
                    delta2 = max(1.0, |B1| / |B2|)
                    p in [0.0, float(c)]
                - True (optional variant): Uses integer floor division as implemented in
                  operating system kernels and storage controllers (e.g., OpenZFS ARC):
                    delta1 = max(1, |B2| // |B1|)
                    delta2 = max(1, |B1| // |B2|)
                    p in [0, c]
        """
        if not isinstance(capacity, int) or capacity < 1:
            raise ValueError(f"Cache capacity must be a positive integer >= 1, got {capacity}")
        
        self.capacity: int = capacity
        self.use_integer_p: bool = use_integer_p
        
        # Adaptive parameter p in [0, c], initially 0.0 (or 0 if integer variant)
        self.p: Union[float, int] = 0 if use_integer_p else 0.0
        
        # The four lists (OrderedDict maintains insertion/access order in O(1))
        # Items are stored as: page_key -> None
        self.T1: OrderedDict[Any, None] = OrderedDict()  # Top recency (resident)
        self.T2: OrderedDict[Any, None] = OrderedDict()  # Top frequency (resident)
        self.B1: OrderedDict[Any, None] = OrderedDict()  # Bottom recency ghost (history only)
        self.B2: OrderedDict[Any, None] = OrderedDict()  # Bottom frequency ghost (history only)
        
        # Performance and telemetry counters
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

    def _replace(self, page_x: Any) -> Tuple[Optional[str], Optional[Any]]:
        """
        Subroutine REPLACE(p, x_t) from FAST 2003 paper (Figure 4).
        
        Decision logic:
          if (|T1| >= 1 and ((x_t in B2 and |T1| == p) or (|T1| > p))):
              move LRU page in T1 to the top (MRU) of B1 and remove page from cache
          else:
              move LRU page in T2 to the top (MRU) of B2 and remove page from cache
              
        Returns:
            Tuple of (eviction_path, evicted_page) or (None, None)
        """
        len_t1 = len(self.T1)
        len_t2 = len(self.T2)
        
        # Exact condition from Megiddo & Modha (2003) Figure 4:
        # if (|T1| >= 1 and ((x_t in B2 and |T1| == p) or (|T1| > p)))
        # Note on fractional p:
        # When p is fractional (e.g., 1.6667), discrete integer |T1| will not equal p,
        # so the condition evaluates based on |T1| > p. The equality condition
        # |T1| == p triggers when p is an exact integer (e.g. 2 == 2.0).
        # We preserve this exact formulation without silent rounding.
        condition_t1 = (len_t1 >= 1) and (
            (page_x in self.B2 and len_t1 == self.p) or (len_t1 > self.p)
        )
        
        # Defensive fallback: In all mathematically reachable ARC states,
        # len(self.T2) >= 1 whenever condition_t1 is False (since |L1| <= c
        # ensures |T1| <= c - 1 when page in B1, guaranteeing |T2| >= 1).
        # This clause is defensive against manual/uninitialized state injection.
        evict_t1 = condition_t1 or (len_t2 == 0)
        
        if evict_t1 and len_t1 > 0:
            # Evict LRU page from T1 and move to top of B1
            lru_page, _ = self.T1.popitem(last=False)
            self.B1[lru_page] = None
            self.B1.move_to_end(lru_page, last=True)
            self.replacements += 1
            return ("T1->B1", lru_page)
        elif len_t2 > 0:
            # Evict LRU page from T2 and move to top of B2
            lru_page, _ = self.T2.popitem(last=False)
            self.B2[lru_page] = None
            self.B2.move_to_end(lru_page, last=True)
            self.replacements += 1
            return ("T2->B2", lru_page)
            
        return (None, None)

    def request(self, page: Any) -> Dict[str, Any]:
        """
        Process a page reference request x_t according to the ARC algorithm.
        Executes one and only one of the four paper cases.
        
        Args:
            page: Identifier of the requested page (e.g. integer or string).
            
        Returns:
            Detailed telemetry dictionary describing the access event, list movements,
            evictions, and post-request cache state.
        """
        self.requests += 1
        p_before = self.p
        delta = 0
        case_name = ""
        hit_type = ""
        movement = ""
        eviction_type = None
        evicted_page = None
        ghost_evicted = None
        
        # -------------------------------------------------------------
        # CASE 1: x_t in T1 U T2 (Cache Hit)
        # -------------------------------------------------------------
        if page in self.T1 or page in self.T2:
            self.hits += 1
            if page in self.T1:
                hit_type = "T1_HIT"
                case_name = "CASE_1_T1_HIT"
                del self.T1[page]
                movement = f"Promoted page {page} from T1 to T2 (MRU)"
            else:
                hit_type = "T2_HIT"
                case_name = "CASE_1_T2_HIT"
                del self.T2[page]
                movement = f"Moved page {page} to T2 (MRU)"
            
            # Place at top (MRU) of T2
            self.T2[page] = None
            self.T2.move_to_end(page, last=True)
            
        # -------------------------------------------------------------
        # CASE 2: x_t in B1 (Ghost Hit in Recency History, Cache Miss)
        # -------------------------------------------------------------
        elif page in self.B1:
            self.misses += 1
            hit_type = "B1_GHOST_HIT"
            case_name = "CASE_2_B1_HIT"
            
            # Learning rule: Expand p favoring recency
            b1_size = len(self.B1)
            b2_size = len(self.B2)
            
            if self.use_integer_p:
                delta = 1 if b1_size >= b2_size else max(1, b2_size // b1_size)
                self.p = min(self.capacity, self.p + delta)
            else:
                delta = 1.0 if b1_size >= b2_size else max(1.0, b2_size / b1_size)
                self.p = min(float(self.capacity), self.p + delta)
                
            # Evict from resident cache to make room
            eviction_type, evicted_page = self._replace(page)
            
            # Move x from B1 to top of T2 and fetch into cache
            del self.B1[page]
            self.T2[page] = None
            self.T2.move_to_end(page, last=True)
            movement = f"B1 ghost hit on {page}; increased p by {delta} to {self.p}; fetched {page} to T2 (MRU)"
            
        # -------------------------------------------------------------
        # CASE 3: x_t in B2 (Ghost Hit in Frequency History, Cache Miss)
        # -------------------------------------------------------------
        elif page in self.B2:
            self.misses += 1
            hit_type = "B2_GHOST_HIT"
            case_name = "CASE_3_B2_HIT"
            
            # Learning rule: Shrink p favoring frequency
            b1_size = len(self.B1)
            b2_size = len(self.B2)
            
            if self.use_integer_p:
                delta = 1 if b2_size >= b1_size else max(1, b1_size // b2_size)
                self.p = max(0, self.p - delta)
            else:
                delta = 1.0 if b2_size >= b1_size else max(1.0, b1_size / b2_size)
                self.p = max(0.0, self.p - delta)
                
            # Evict from resident cache to make room
            eviction_type, evicted_page = self._replace(page)
            
            # Move x from B2 to top of T2 and fetch into cache
            del self.B2[page]
            self.T2[page] = None
            self.T2.move_to_end(page, last=True)
            movement = f"B2 ghost hit on {page}; decreased p by {delta} to {self.p}; fetched {page} to T2 (MRU)"
            
        # -------------------------------------------------------------
        # CASE 4: x_t not in (T1 U B1 U T2 U B2) (Cache & History Miss)
        # -------------------------------------------------------------
        else:
            self.misses += 1
            hit_type = "MISS"
            len_l1 = len(self.T1) + len(self.B1)
            len_l2 = len(self.T2) + len(self.B2)
            
            # Sub-case A: |L1| == c
            if len_l1 == self.capacity:
                case_name = "CASE_4A_L1_FULL"
                if len(self.T1) < self.capacity:
                    # Delete LRU page in B1
                    ghost_evicted, _ = self.B1.popitem(last=False)
                    eviction_type, evicted_page = self._replace(page)
                    movement = f"New miss on {page}; purged ghost {ghost_evicted} from B1; fetched {page} to T1 (MRU)"
                else:
                    # T1 is completely full: delete LRU page in T1
                    evicted_page, _ = self.T1.popitem(last=False)
                    eviction_type = "T1_EVICT"
                    self.replacements += 1
                    movement = f"New miss on {page}; evicted LRU {evicted_page} from T1; fetched {page} to T1 (MRU)"
            
            # Sub-case B: |L1| < c
            else:
                case_name = "CASE_4B_L1_LESS"
                total_len = len_l1 + len_l2
                if total_len >= self.capacity:
                    if total_len == 2 * self.capacity:
                        # Directory is completely full: delete LRU page in B2
                        ghost_evicted, _ = self.B2.popitem(last=False)
                    eviction_type, evicted_page = self._replace(page)
                    movement = f"New miss on {page}; fetched {page} to T1 (MRU)"
                    if ghost_evicted is not None:
                        movement += f"; purged ghost {ghost_evicted} from B2"
                else:
                    # Warmup phase: cache not full yet
                    movement = f"Cache warmup: inserted {page} to T1 (MRU)"
                    
            # Finally, insert x_t into MRU (top) of T1
            self.T1[page] = None
            self.T1.move_to_end(page, last=True)
            
        # Verify paper invariants
        self._verify_invariants()
        
        return {
            "page": page,
            "hit": (hit_type in ("T1_HIT", "T2_HIT")),
            "hit_type": hit_type,
            "case": case_name,
            "p_before": p_before,
            "p_after": self.p,
            "delta": delta,
            "eviction_type": eviction_type,
            "evicted_page": evicted_page,
            "ghost_evicted": ghost_evicted,
            "movement": movement,
            "state": self.get_state()
        }

    def _verify_invariants(self) -> None:
        """
        Verify all 5 mathematical invariants asserted in Section 3 of the paper.
        Raises AssertionError if an invariant is violated.
        """
        len_t1 = len(self.T1)
        len_t2 = len(self.T2)
        len_b1 = len(self.B1)
        len_b2 = len(self.B2)
        
        # 1. Active cache size bounded by capacity c
        assert len_t1 + len_t2 <= self.capacity, (
            f"Invariant Violated: |T1| + |T2| = {len_t1 + len_t2} > c = {self.capacity}"
        )
        
        # 2. Total tracked directory bounded by 2c
        assert len_t1 + len_b1 + len_t2 + len_b2 <= 2 * self.capacity, (
            f"Invariant Violated: Total history = {len_t1 + len_b1 + len_t2 + len_b2} > 2c = {2 * self.capacity}"
        )
        
        # 3. L1 capacity bounded by c
        assert len_t1 + len_b1 <= self.capacity, (
            f"Invariant Violated: |L1| = {len_t1 + len_b1} > c = {self.capacity}"
        )
        
        # 4. Target parameter p bounded by [0, c]
        assert 0 <= self.p <= self.capacity, (
            f"Invariant Violated: p = {self.p} outside [0, {self.capacity}]"
        )
        
        # 5. Mutual exclusivity / disjointness between all 4 lists
        set_t1 = set(self.T1.keys())
        set_t2 = set(self.T2.keys())
        set_b1 = set(self.B1.keys())
        set_b2 = set(self.B2.keys())
        
        assert set_t1.isdisjoint(set_t2), "T1 and T2 must be disjoint"
        assert set_b1.isdisjoint(set_b2), "B1 and B2 must be disjoint"
        assert (set_t1 | set_b1).isdisjoint(set_t2 | set_b2), "L1 and L2 must be disjoint"

    def get_state(self) -> Dict[str, Any]:
        """
        Capture current snapshot of the ARC cache state.
        
        Note on list ordering:
          Lists are returned ordered from MRU (top, index 0) to LRU (bottom, last index).
        """
        return {
            "capacity": self.capacity,
            "p": self.p,
            "t1_target": self.p,
            "t2_target": self.capacity - self.p,
            "T1": list(reversed(self.T1.keys())),  # MRU -> LRU
            "T2": list(reversed(self.T2.keys())),  # MRU -> LRU
            "B1": list(reversed(self.B1.keys())),  # MRU -> LRU
            "B2": list(reversed(self.B2.keys())),  # MRU -> LRU
            "size_T1": len(self.T1),
            "size_T2": len(self.T2),
            "size_B1": len(self.B1),
            "size_B2": len(self.B2),
            "size_cache": len(self.T1) + len(self.T2),
            "size_total": len(self.T1) + len(self.T2) + len(self.B1) + len(self.B2),
            "requests": self.requests,
            "hits": self.hits,
            "misses": self.misses,
            "hit_ratio": self.hit_ratio,
            "replacements": self.replacements,
            "order": "MRU_to_LRU"
        }

    def reset(self) -> None:
        """Reset cache to empty initial state."""
        self.p = 0 if self.use_integer_p else 0.0
        self.T1.clear()
        self.T2.clear()
        self.B1.clear()
        self.B2.clear()
        self.requests = 0
        self.hits = 0
        self.misses = 0
        self.replacements = 0
