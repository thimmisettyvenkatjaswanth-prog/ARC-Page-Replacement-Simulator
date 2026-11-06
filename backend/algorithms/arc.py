"""
Adaptive Replacement Cache (ARC) Algorithm
Faithful implementation of the research paper:
"ARC: A Self-Tuning, Low Overhead Replacement Cache"
by Nimrod Megiddo and Dharmendra S. Modha (USENIX FAST 2003).

This module is completely independent of FastAPI and GUI frameworks.
"""
from collections import OrderedDict
from dataclasses import dataclass, replace
from typing import Any, Callable, Dict, List, Optional, Tuple, Union

from backend.memory.exceptions import (
    BindingError,
    StaleTokenError,
    UnsupportedOperationError,
)


@dataclass(frozen=True)
class ArcPlan:
    """
    Immutable plan produced by ARCCache during read-only pre-validation.
    
    Contains all computed decisions needed for two-phase commit:
      - page: The referenced page.
      - case: The ARC case classification ("CASE_1_T1_HIT", "CASE_1_T2_HIT", "CASE_2_B1_HIT",
              "CASE_3_B2_HIT", "CASE_4A_L1_FULL", "CASE_4B_L1_LESS").
      - is_hit: True if page is resident in T1 or T2.
      - requires_eviction: True if active resident cache requires victim eviction.
      - target_p: The new value of target parameter p after this request.
      - delta: The magnitude of p adaptation.
      - state_version: Monotonically increasing cache version at plan creation.
      - victim: The candidate resident page to evict (if requires_eviction is True).
      - eviction_type: "T1->B1", "T2->B2", "T1_EVICT", or None.
      - ghost_evicted: History ghost page purged from B1 or B2 (if directory trimming needed).
      - directory_signature: Complete ordered structural fingerprint of T1, T2, B1, B2, and p at plan creation.
    """
    page: Any
    case: str
    is_hit: bool
    requires_eviction: bool
    target_p: Union[float, int]
    delta: Union[float, int]
    state_version: int
    victim: Optional[Any] = None
    eviction_type: Optional[str] = None
    ghost_evicted: Optional[Any] = None
    directory_signature: Optional[Tuple[Any, ...]] = None


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
        
        # Monotonically increasing state version
        self.state_version: int = 0
        
        # Integration flag enforcing single authoritative manager ownership
        self._is_integrated: bool = False
        
        # Active multi-step commit sequence binding
        self._active_commit_plan: Optional[ArcPlan] = None
        
        # Pending transaction plan (fallback)
        self._pending_plan: Optional[ArcPlan] = None

    def bind_to_manager(self) -> None:
        """
        Bind this ARCCache instance to MemoryManager integrated mode.
        Enforces a one-way transition from a pristine standalone instance to integrated mode.
        
        Raises:
            BindingError: If cache is already integrated or not pristine.
        """
        if self._is_integrated:
            raise BindingError("ARCCache instance is already bound to MemoryManager.")
        if (
            self.requests > 0
            or len(self.T1) > 0
            or len(self.T2) > 0
            or len(self.B1) > 0
            or len(self.B2) > 0
        ):
            raise BindingError("Cannot bind a non-pristine ARCCache instance to MemoryManager.")
        self._is_integrated = True

    @property
    def hit_ratio(self) -> float:
        """Calculate current cumulative hit ratio."""
        if self.requests == 0:
            return 0.0
        return round(self.hits / self.requests, 4)

    def _evaluate_replace(
        self,
        page_x: Any,
        target_p: Union[float, int],
        is_eligible_cb: Optional[Callable[[Any], bool]] = None
    ) -> Tuple[Optional[str], Optional[Any]]:
        """
        Pure read-only evaluation of ARC REPLACE subroutine using target_p and optional eligibility filter.
        
        Evaluates candidate victim and list source without mutating T1, T2, B1, B2,
        target parameter p, or counters.
        
        Returns:
            Tuple of (eviction_type, victim_page) or (None, None).
        """
        len_t1 = len(self.T1)
        len_t2 = len(self.T2)
        
        condition_t1 = (len_t1 >= 1) and (
            (page_x in self.B2 and len_t1 == target_p) or (len_t1 > target_p)
        )
        evict_t1 = condition_t1 or (len_t2 == 0)
        
        if is_eligible_cb is None:
            if evict_t1 and len_t1 > 0:
                return ("T1->B1", next(iter(self.T1)))
            elif len_t2 > 0:
                return ("T2->B2", next(iter(self.T2)))
            return (None, None)

        # Quota-constrained eligibility search with alternate-list fallback
        primary_list, primary_type = (self.T1, "T1->B1") if evict_t1 else (self.T2, "T2->B2")
        alternate_list, alternate_type = (self.T2, "T2->B2") if evict_t1 else (self.T1, "T1->B1")

        # 1. Search primary list in LRU-to-MRU order
        for page in primary_list:
            if is_eligible_cb(page):
                return (primary_type, page)

        # 2. Fallback: Search alternate list in LRU-to-MRU order
        for page in alternate_list:
            if is_eligible_cb(page):
                return (alternate_type, page)

        # 3. No eligible victim in either list
        return (None, None)

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

        Raises:
            UnsupportedOperationError: If cache is integrated with MemoryManager.
        """
        if self._is_integrated:
            raise UnsupportedOperationError(
                "Direct call to ARCCache.request() is unsupported in integrated mode. "
                "All memory accesses must be coordinated through MemoryManager.access_page()."
            )

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
        self.state_version += 1
        
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
            "state_version": self.state_version,
            "is_integrated": self._is_integrated,
            "order": "MRU_to_LRU"
        }

    def reset(self) -> None:
        """
        Reset standalone cache to empty initial state.
        
        Raises:
            UnsupportedOperationError: If cache is integrated with MemoryManager.
        """
        if self._is_integrated:
            raise UnsupportedOperationError(
                "Direct call to ARCCache.reset() is unsupported in integrated mode. "
                "Reset must be coordinated through MemoryManager.reset()."
            )
        self._reset_internal()

    def _coordinated_reset(self) -> None:
        """
        Coordinated reset path invoked exclusively by MemoryManager.reset().
        Preserves integrated mode and monotonically advances state_version to invalidate prior plans.
        """
        self._reset_internal()

    def _reset_internal(self) -> None:
        """Internal reset implementation shared by standalone and coordinated reset."""
        self.p = 0 if self.use_integer_p else 0.0
        self.T1.clear()
        self.T2.clear()
        self.B1.clear()
        self.B2.clear()
        self.requests = 0
        self.hits = 0
        self.misses = 0
        self.replacements = 0
        self._active_commit_plan = None
        self._pending_plan = None
        # Monotonically advance state_version across reset to invalidate pre-reset plans
        self.state_version += 1

    # =========================================================================
    # Decoupled Lifecycle Methods for MemoryManager Integration (Phase 3A)
    # =========================================================================

    def _get_directory_signature(self) -> Tuple[Any, ...]:
        """
        Compute complete ordered structural fingerprint of resident and ghost directories.
        
        Covers the exact ordered sequence of every key in T1, T2, B1, and B2, as well as
        target parameter p. Any external mutation—including additions, deletions, or
        reordering of middle elements—diverges this signature and rejects stale plans.
        """
        return (
            tuple(self.T1.keys()),
            tuple(self.T2.keys()),
            tuple(self.B1.keys()),
            tuple(self.B2.keys()),
            self.p,
        )

    def contains(self, page: Any) -> bool:
        """Return True if page is resident in cache (T1 or T2)."""
        return (page in self.T1 or page in self.T2)

    def prepare_request(
        self,
        page: Any,
        process_context: Optional[int] = None
    ) -> ArcPlan:
        """
        Pure read-only pre-validation and planning hook for MemoryManager Phase 1.
        
        Evaluates the requested page against ARC directory lists (T1, T2, B1, B2),
        calculates target parameter p adaptation without mutating self.p, evaluates
        victim selection via REPLACE using target_p, captures the current state_version,
        and notes any directory trimming.
        
        Returns:
            Immutable ArcPlan describing the access decisions.
        """
        current_version = self.state_version
        current_sig = self._get_directory_signature()

        # Case 1: Resident Hit in T1 or T2
        if page in self.T1 or page in self.T2:
            case = "CASE_1_T1_HIT" if page in self.T1 else "CASE_1_T2_HIT"
            plan = ArcPlan(
                page=page,
                case=case,
                is_hit=True,
                requires_eviction=False,
                target_p=self.p,
                delta=0 if self.use_integer_p else 0.0,
                state_version=current_version,
                victim=None,
                eviction_type=None,
                ghost_evicted=None,
                directory_signature=current_sig,
            )
            self._pending_plan = plan
            return plan

        # Case 2: Ghost Hit in B1 (Recency History)
        elif page in self.B1:
            b1_size = len(self.B1)
            b2_size = len(self.B2)
            if self.use_integer_p:
                delta = 1 if b1_size >= b2_size else max(1, b2_size // b1_size)
                target_p = min(self.capacity, self.p + delta)
            else:
                delta = 1.0 if b1_size >= b2_size else max(1.0, b2_size / b1_size)
                target_p = min(float(self.capacity), self.p + delta)

            eviction_type, victim = self._evaluate_replace(page, target_p)
            plan = ArcPlan(
                page=page,
                case="CASE_2_B1_HIT",
                is_hit=False,
                requires_eviction=(victim is not None),
                target_p=target_p,
                delta=delta,
                state_version=current_version,
                victim=victim,
                eviction_type=eviction_type,
                ghost_evicted=None,
                directory_signature=current_sig,
            )
            self._pending_plan = plan
            return plan

        # Case 3: Ghost Hit in B2 (Frequency History)
        elif page in self.B2:
            b1_size = len(self.B1)
            b2_size = len(self.B2)
            if self.use_integer_p:
                delta = 1 if b2_size >= b1_size else max(1, b1_size // b2_size)
                target_p = max(0, self.p - delta)
            else:
                delta = 1.0 if b2_size >= b1_size else max(1.0, b1_size / b2_size)
                target_p = max(0.0, self.p - delta)

            eviction_type, victim = self._evaluate_replace(page, target_p)
            plan = ArcPlan(
                page=page,
                case="CASE_3_B2_HIT",
                is_hit=False,
                requires_eviction=(victim is not None),
                target_p=target_p,
                delta=delta,
                state_version=current_version,
                victim=victim,
                eviction_type=eviction_type,
                ghost_evicted=None,
                directory_signature=current_sig,
            )
            self._pending_plan = plan
            return plan

        # Case 4: Cache & History Miss (Cold Miss)
        else:
            len_l1 = len(self.T1) + len(self.B1)
            len_l2 = len(self.T2) + len(self.B2)
            target_p = self.p
            delta = 0 if self.use_integer_p else 0.0

            if len_l1 == self.capacity:
                # Subcase 4A: |L1| == c
                if len(self.T1) < self.capacity:
                    ghost_evicted = next(iter(self.B1)) if self.B1 else None
                    eviction_type, victim = self._evaluate_replace(page, target_p)
                    plan = ArcPlan(
                        page=page,
                        case="CASE_4A_L1_FULL",
                        is_hit=False,
                        requires_eviction=(victim is not None),
                        target_p=target_p,
                        delta=delta,
                        state_version=current_version,
                        victim=victim,
                        eviction_type=eviction_type,
                        ghost_evicted=ghost_evicted,
                        directory_signature=current_sig,
                    )
                else:
                    # |T1| == c: Direct T1 drop without moving to B1 (paper Figure 4, Case IVa subcase 1b)
                    victim = next(iter(self.T1)) if self.T1 else None
                    plan = ArcPlan(
                        page=page,
                        case="CASE_4A_L1_FULL",
                        is_hit=False,
                        requires_eviction=(victim is not None),
                        target_p=target_p,
                        delta=delta,
                        state_version=current_version,
                        victim=victim,
                        eviction_type="T1_EVICT",
                        ghost_evicted=None,
                        directory_signature=current_sig,
                    )
                self._pending_plan = plan
                return plan

            else:
                # Subcase 4B: |L1| < c
                total_len = len_l1 + len_l2
                if total_len >= self.capacity:
                    ghost_evicted = next(iter(self.B2)) if (total_len == 2 * self.capacity and self.B2) else None
                    eviction_type, victim = self._evaluate_replace(page, target_p)
                    plan = ArcPlan(
                        page=page,
                        case="CASE_4B_L1_LESS",
                        is_hit=False,
                        requires_eviction=(victim is not None),
                        target_p=target_p,
                        delta=delta,
                        state_version=current_version,
                        victim=victim,
                        eviction_type=eviction_type,
                        ghost_evicted=ghost_evicted,
                        directory_signature=current_sig,
                    )
                else:
                    # Warmup: cache not full yet, no eviction needed
                    plan = ArcPlan(
                        page=page,
                        case="CASE_4B_L1_LESS",
                        is_hit=False,
                        requires_eviction=False,
                        target_p=target_p,
                        delta=delta,
                        state_version=current_version,
                        victim=None,
                        eviction_type=None,
                        ghost_evicted=None,
                        directory_signature=current_sig,
                    )
                self._pending_plan = plan
                return plan

    def _validate_and_bind_plan(self, plan: Optional[Any]) -> ArcPlan:
        """
        Validate plan against current state_version and directory signature.
        Binds the active commit session to this plan for multi-step commit.
        
        Raises:
            StaleTokenError: If plan is missing, stale, or mismatched with active commit.
        """
        if plan is None:
            if self._pending_plan is not None:
                plan = self._pending_plan
            else:
                raise StaleTokenError("A valid ArcPlan token is required for integrated commit.")

        if not isinstance(plan, ArcPlan):
            raise StaleTokenError(f"Expected ArcPlan instance, got {type(plan).__name__}")

        if self._active_commit_plan is not None:
            # Commit sequence already in progress for this transaction
            if plan is not self._active_commit_plan and plan != self._active_commit_plan:
                raise StaleTokenError(
                    f"Active commit sequence bound to plan for page {self._active_commit_plan.page}; "
                    f"rejected mismatched plan for page {plan.page}."
                )
            return self._active_commit_plan

        # First operation in commit sequence: validate against current state_version
        if plan.state_version != self.state_version:
            raise StaleTokenError(
                f"Stale ArcPlan rejected: plan version {plan.state_version} does not match "
                f"current cache version {self.state_version}."
            )

        current_sig = self._get_directory_signature()
        if plan.directory_signature is not None and plan.directory_signature != current_sig:
            raise StaleTokenError(
                f"Stale ArcPlan rejected: cache directory was modified since plan preparation "
                f"(plan signature {plan.directory_signature} != current signature {current_sig})."
            )

        # Bind active commit session to this exact plan
        self._active_commit_plan = plan
        return plan

    def requires_eviction(
        self,
        page: Optional[Any] = None,
        plan: Optional[ArcPlan] = None
    ) -> bool:
        """
        Check whether ARC mandates an eviction for the given access.
        """
        effective_plan = plan if isinstance(plan, ArcPlan) else None
        if effective_plan is None:
            if page is not None and (self._pending_plan is None or self._pending_plan.page != page):
                effective_plan = self.prepare_request(page)
            else:
                effective_plan = self._pending_plan

        if effective_plan is not None:
            return effective_plan.requires_eviction

        return (len(self.T1) + len(self.T2)) >= self.capacity

    def select_victim(
        self,
        plan: Optional[ArcPlan] = None,
        is_eligible_cb: Optional[Callable[[Any], bool]] = None
    ) -> Any:
        """
        Pure read-only hook: Return candidate victim page without mutating ARC state.
        
        Args:
            plan: Optional active ArcPlan.
            is_eligible_cb: Optional callback filtering candidate victim eligibility.
            
        Returns:
            The selected victim page key, or None if no eligible victim exists.
        """
        effective_plan = plan if isinstance(plan, ArcPlan) else self._pending_plan

        if is_eligible_cb is None:
            if effective_plan is not None and effective_plan.victim is not None:
                return effective_plan.victim
            eviction_type, victim = self._evaluate_replace(None, self.p)
            return victim

        # Eligibility callback provided: evaluate with quota-constrained fallback
        target_p = effective_plan.target_p if effective_plan is not None else self.p
        req_page = effective_plan.page if effective_plan is not None else None

        # Check Case 4A direct T1 drop subcase
        if (
            effective_plan is not None
            and effective_plan.case == "CASE_4A_L1_FULL"
            and len(self.T1) == self.capacity
        ):
            # Special direct T1 drop: search T1 only
            matched_victim = None
            for page in self.T1:
                if is_eligible_cb(page):
                    matched_victim = page
                    break
            new_plan = replace(
                effective_plan,
                victim=matched_victim,
                eviction_type="T1_EVICT" if matched_victim is not None else None,
                requires_eviction=(matched_victim is not None)
            )
            self._pending_plan = new_plan
            return matched_victim

        eviction_type, victim = self._evaluate_replace(req_page, target_p, is_eligible_cb)
        if effective_plan is not None:
            new_plan = replace(
                effective_plan,
                victim=victim,
                eviction_type=eviction_type,
                requires_eviction=(victim is not None)
            )
            self._pending_plan = new_plan
        return victim

    def evict_victim(self, victim: Any, plan: Optional[Any] = None) -> None:
        """
        Phase 3 commit hook: Evict the selected victim from resident cache (T1 or T2).
        Moves victim to B1 or B2, or directly purges it if T1 is at full capacity (Case 4A).
        Also purges ghost entries (directory trimming) from B1 or B2 if planned.
        """
        effective_plan = self._validate_and_bind_plan(plan)

        # 1. Directory trimming (ghost purge) if required by Case 4A or 4B
        if effective_plan.ghost_evicted is not None:
            if effective_plan.case == "CASE_4A_L1_FULL" and self.B1:
                self.B1.popitem(last=False)
            elif effective_plan.case == "CASE_4B_L1_LESS" and self.B2:
                self.B2.popitem(last=False)

        # 2. Evict resident victim
        eviction_type = effective_plan.eviction_type

        if eviction_type == "T1_EVICT":
            # Direct drop from T1 without moving to B1 (Case 4A subcase 1b when |T1| == c)
            if victim in self.T1:
                del self.T1[victim]
            self.replacements += 1
        elif eviction_type == "T1->B1" or (eviction_type is None and victim in self.T1):
            if victim in self.T1:
                del self.T1[victim]
            self.B1[victim] = None
            self.B1.move_to_end(victim, last=True)
            self.replacements += 1
        elif eviction_type == "T2->B2" or (eviction_type is None and victim in self.T2):
            if victim in self.T2:
                del self.T2[victim]
            self.B2[victim] = None
            self.B2.move_to_end(victim, last=True)
            self.replacements += 1
        else:
            if victim in self.T1:
                del self.T1[victim]
                self.B1[victim] = None
                self.B1.move_to_end(victim, last=True)
                self.replacements += 1
            elif victim in self.T2:
                del self.T2[victim]
                self.B2[victim] = None
                self.B2.move_to_end(victim, last=True)
                self.replacements += 1

    def insert_page(
        self,
        page: Any,
        frame_id: Optional[int] = None,
        plan: Optional[Any] = None
    ) -> None:
        """
        Phase 3 commit hook: Insert incoming page on miss into T1 or T2.
        Applies p adaptation and removes from ghost list if ghost hit.
        """
        effective_plan = self._validate_and_bind_plan(plan)

        # Commit adapted p
        self.p = effective_plan.target_p

        # Remove from ghost list if ghost hit
        if effective_plan.case == "CASE_2_B1_HIT" and page in self.B1:
            del self.B1[page]
        elif effective_plan.case == "CASE_3_B2_HIT" and page in self.B2:
            del self.B2[page]

        # Place page into T2 (ghost hit) or T1 (cold miss)
        if effective_plan.case in ("CASE_2_B1_HIT", "CASE_3_B2_HIT"):
            self.T2[page] = None
            self.T2.move_to_end(page, last=True)
        else:
            self.T1[page] = None
            self.T1.move_to_end(page, last=True)

        self.requests += 1
        self.misses += 1
        self._verify_invariants()

    def record_hit(self, page: Any, plan: Optional[Any] = None) -> None:
        """
        Phase 3 commit hook: Update ARC lists upon a resident cache hit (Case 1).
        Promotes page to MRU of T2.
        """
        effective_plan = self._validate_and_bind_plan(plan)

        if page in self.T1:
            del self.T1[page]
        elif page in self.T2:
            del self.T2[page]

        self.T2[page] = None
        self.T2.move_to_end(page, last=True)

        self.requests += 1
        self.hits += 1
        self._verify_invariants()

    def commit_request(self, token: Optional[Any] = None, is_hit: bool = False) -> None:
        """
        Finalize transaction commit: advances state_version monotonically and clears active session.
        """
        if self._active_commit_plan is not None:
            if token is not None and isinstance(token, ArcPlan) and token != self._active_commit_plan:
                raise StaleTokenError(
                    f"Mismatched token in commit_request: expected {self._active_commit_plan.page}, got {token.page}"
                )
            # Transaction successfully completed: advance state_version
            self.state_version += 1
            self._active_commit_plan = None
        self._pending_plan = None

    def abort_request(self, token: Optional[Any] = None) -> None:
        """Clear uncommitted plan and active commit session on transaction abort."""
        self._active_commit_plan = None
        self._pending_plan = None

    def purge_pid(self, pid: int) -> int:
        """
        Purge all pages belonging to the specified PID from the ARC cache.
        Removes matching pages from T1, B1, T2, and B2.
        Preserves the ARC adaptation invariant 0 <= p <= capacity without clamping to resident count.
        Non-process keys without a .pid attribute are safely ignored.
        
        Raises:
            UnsupportedOperationError: If an integrated ARC transaction is active.
            
        Returns:
            Count of resident and ghost entries purged.
        """
        if self._pending_plan is not None or self._active_commit_plan is not None:
            raise UnsupportedOperationError(
                f"Cannot purge process {pid}: an integrated ARC transaction is active."
            )

        purged_count = 0
        for d in (self.T1, self.B1, self.T2, self.B2):
            keys_to_remove = [k for k in d if hasattr(k, "pid") and k.pid == pid]
            for k in keys_to_remove:
                del d[k]
                purged_count += 1

        # Preserve invariant 0 <= p <= capacity
        if self.use_integer_p:
            self.p = min(max(0, int(self.p)), self.capacity)
        else:
            self.p = min(max(0.0, float(self.p)), float(self.capacity))

        if purged_count > 0:
            self.state_version += 1

        return purged_count
