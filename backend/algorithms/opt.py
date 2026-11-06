"""
Optimal (OPT / Belady's MIN) Page Replacement Algorithm.
Offline Clairvoyant Baseline Implementation for Comparative Analysis and
three-phase transactional OS MemoryManager integration.

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
from dataclasses import dataclass
from typing import Any, Callable, Dict, List, Optional, Sequence, Set, Tuple, Union

from backend.memory.exceptions import (
    BindingError,
    StaleTokenError,
    UnsupportedOperationError,
)
from backend.memory.page import VirtualPage


@dataclass(frozen=True)
class OptValidationToken:
    """
    Immutable request validation token produced by OptimalCache during Phase 1 pre-validation.
    """
    page: Any
    expected_page: Any
    current_step: int
    is_hit: bool
    state_version: int
    future_distance: float
    status: str = "HIT"
    requires_eviction: bool = False

    def __getitem__(self, item: str) -> Any:
        if item == "status":
            return self.status
        if item == "requires_eviction":
            return self.requires_eviction
        raise KeyError(item)


@dataclass(frozen=True)
class OptVictimToken:
    """
    Immutable victim selection token produced by OptimalCache during Phase 1 planning.
    
    Contains all computed decisions needed for protected commit:
      - victim_page: The virtual page selected for eviction.
      - victim_frame_id: The physical frame slot of the victim.
      - current_step: Sequence step at planning time.
      - state_version: Monotonically increasing cache version at plan creation.
      - future_distance: Precomputed future distance to next reference (or infinity).
      - resident_snapshot: Ordered tuple of resident page keys at planning time.
      - expected_page: Incoming virtual page being referenced.
      - future_indices_snapshot: Snapshot of relevant future index queues.
    """
    victim_page: Any
    victim_frame_id: int
    current_step: int
    state_version: int
    future_distance: float
    resident_snapshot: Tuple[Any, ...]
    expected_page: Any
    future_indices_snapshot: Tuple[Tuple[Any, Tuple[int, ...]], ...]

    def __eq__(self, other: Any) -> bool:
        if isinstance(other, OptVictimToken):
            return (
                self.victim_page == other.victim_page
                and self.victim_frame_id == other.victim_frame_id
                and self.current_step == other.current_step
                and self.state_version == other.state_version
                and self.future_distance == other.future_distance
                and self.resident_snapshot == other.resident_snapshot
                and self.expected_page == other.expected_page
                and self.future_indices_snapshot == other.future_indices_snapshot
            )
        return self.victim_page == other


class OptimalCache:
    """
    Belady's Optimal (OPT / MIN) Cache Implementation.
    
    Offline Clairvoyant Policy:
      - Requires the full future reference sequence prior to simulation.
      - Precomputes future occurrence index queues per distinct page.
      - Evicts the resident page whose next occurrence index is furthest in the future.
      - Breaks ties using deterministic FIFO arrival order in OrderedDict.
    """

    def __init__(self, capacity: int, reference_sequence: Optional[Sequence[Any]] = None):
        """
        Initialize Optimal Cache.
        
        Args:
            capacity: Maximum number of pages the cache can hold (integer >= 1).
            reference_sequence: Optional full future sequence of page references.
        """
        if not isinstance(capacity, int) or isinstance(capacity, bool) or capacity < 1:
            raise ValueError(f"Cache capacity must be a positive integer >= 1, got {capacity!r}")
            
        self.capacity: int = capacity
        
        # Resident pages tracked in OrderedDict for deterministic FIFO tie-breaking: page -> arrival_step
        self.cache: OrderedDict[Any, int] = OrderedDict()
        self.page_to_frame: Dict[Any, int] = {}
        
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
        
        # Versioning & Integration State
        self.state_version: int = 0
        self._is_integrated: bool = False
        
        # Transaction commit session tokens
        self._active_commit_token: Optional[Any] = None
        self._pending_token: Optional[Any] = None
        
        if reference_sequence is not None:
            self.initialize_sequence(reference_sequence)

    @property
    def hit_ratio(self) -> float:
        """Calculate current cumulative hit ratio."""
        if self.requests == 0:
            return 0.0
        return round(self.hits / self.requests, 4)

    @property
    def size(self) -> int:
        """Current number of cached pages."""
        return len(self.cache)

    def contains(self, page: Any) -> bool:
        """Check if page is currently resident in cache."""
        p = self._normalize_page(page) if self._is_integrated else page
        return p in self.cache

    def is_full(self) -> bool:
        """Check if resident cache has reached maximum capacity."""
        return len(self.cache) >= self.capacity

    def requires_eviction(self, vpage: Optional[Any] = None, token: Optional[Any] = None) -> bool:
        """Check whether policy requires an eviction for the current access."""
        return len(self.cache) >= self.capacity

    def _normalize_page(self, page: Any) -> Any:
        """Normalize page references to VirtualPage identity in integrated mode."""
        if isinstance(page, VirtualPage):
            return page
        if isinstance(page, (tuple, list)) and len(page) == 2 and isinstance(page[0], int) and isinstance(page[1], int) and not isinstance(page[0], bool) and not isinstance(page[1], bool):
            return VirtualPage(pid=page[0], vpn=page[1])
        if isinstance(page, int) and not isinstance(page, bool):
            return VirtualPage(pid=1, vpn=page)
        return page

    def bind_to_manager(self, reference_sequence: Optional[Sequence[Any]] = None) -> None:
        """
        Bind this OptimalCache instance to MemoryManager integrated mode.
        Enforces a one-way transition from a pristine standalone instance to integrated mode.
        
        Raises:
            BindingError: If cache is already integrated or not pristine.
            ValueError: If reference sequence is missing or empty.
        """
        if self._is_integrated:
            raise BindingError("OptimalCache instance is already bound to MemoryManager.")
        if (
            self.requests > 0
            or self.hits > 0
            or self.misses > 0
            or self.replacements > 0
            or len(self.cache) > 0
            or self.current_step > 0
            or self.state_version != 0
        ):
            raise BindingError("Cannot bind a non-pristine OptimalCache instance to MemoryManager.")

        if reference_sequence is not None:
            self.sequence = list(reference_sequence)

        if not self.sequence:
            raise ValueError("Cannot bind OptimalCache without an initialized reference sequence.")

        # Adapt sequence elements to VirtualPage for integrated mode consistency
        norm_seq = [self._normalize_page(p) for p in self.sequence]
        self.initialize_sequence(norm_seq)

        self._is_integrated = True

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
        Process a page reference request according to Belady's Optimal policy (standalone only).
        
        Raises:
            UnsupportedOperationError: If cache is integrated with MemoryManager.
            ValueError: If reference sequence was never initialized, exhausted, or mismatched.
        """
        if self._is_integrated:
            raise UnsupportedOperationError(
                "Direct call to OptimalCache.request() is unsupported in integrated mode. "
                "All memory accesses must be coordinated through MemoryManager.access_page()."
            )

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
        self.state_version += 1
        
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

    def prepare_request(self, incoming_page: Any, process_context: Optional[int] = None) -> Any:
        """
        Classify incoming request without mutating cache state or sequence indices.
        
        Raises:
            ValueError: If reference sequence is not initialized, exhausted, or mismatched.
        """
        norm_page = self._normalize_page(incoming_page) if self._is_integrated else incoming_page

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
        if norm_page != expected_page:
            raise ValueError(
                f"Sequence mismatch at index {self.current_step}: "
                f"expected page {expected_page!r}, received page {norm_page!r}. "
                f"OPT requires requests to strictly match the initialized reference sequence."
            )

        is_hit = norm_page in self.cache
        future_dist = self._future_distance(norm_page)

        # Standalone untokenized legacy backward-compatibility
        if not self._is_integrated:
            if norm_page in self.page_to_future_indices and self.page_to_future_indices[norm_page]:
                if self.page_to_future_indices[norm_page][0] == self.current_step:
                    self.page_to_future_indices[norm_page].popleft()
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

        token = OptValidationToken(
            page=norm_page,
            expected_page=expected_page,
            current_step=self.current_step,
            is_hit=is_hit,
            state_version=self.state_version,
            future_distance=future_dist,
            status="HIT" if is_hit else "COLD_MISS",
            requires_eviction=len(self.cache) >= self.capacity,
        )
        self._pending_token = token
        return token

    def select_victim(
        self,
        incoming_page: Any = None,
        process_context: Optional[int] = None,
        token: Optional[Any] = None,
        is_eligible_cb: Optional[Callable[[Any], bool]] = None
    ) -> Any:
        """
        Identify the candidate victim page with the furthest future reference distance.
        Does not mutate resident cache or future index queues.
        If is_eligible_cb is provided, only candidates where is_eligible_cb(page) is True are considered.
        """
        if not self.cache or len(self.cache) < self.capacity:
            return None
            
        victim = None
        max_dist = -1.0
        
        # Ties broken deterministically by earliest arrival order in OrderedDict (FIFO)
        for res_page in self.cache:
            if is_eligible_cb is not None and not is_eligible_cb(res_page):
                continue
            dist = self._future_distance(res_page)
            if dist > max_dist:
                max_dist = dist
                victim = res_page

        if victim is None:
            return None

        if not self._is_integrated and token is None:
            # Standalone untokenized returns victim page directly for backward compatibility
            return victim

        victim_fid = self.page_to_frame.get(victim, 0)
        resident_snapshot = tuple(self.cache.keys())
        expected_page = self.sequence[self.current_step] if self.current_step < len(self.sequence) else None
        future_indices_snapshot = tuple(
            (p, tuple(self.page_to_future_indices.get(p, ())))
            for p in resident_snapshot
        )

        victim_token = OptVictimToken(
            victim_page=victim,
            victim_frame_id=victim_fid,
            current_step=self.current_step,
            state_version=self.state_version,
            future_distance=max_dist,
            resident_snapshot=resident_snapshot,
            expected_page=expected_page,
            future_indices_snapshot=future_indices_snapshot,
        )
        self._pending_token = victim_token
        return victim_token

    def _validate_and_bind_token(self, token: Optional[Any]) -> Any:
        """
        Validate token against current state_version, step, resident cache, and future index queues.
        Binds the active commit session to this token.
        
        Raises:
            StaleTokenError: If token is missing, stale, or mismatched.
        """
        if not self._is_integrated and token is None:
            # Standalone untokenized operations bypass token validation and session binding
            return None

        if token is None:
            if self._pending_token is not None:
                token = self._pending_token
            else:
                raise StaleTokenError("A valid OPT token is required for commit.")

        if self._active_commit_token is not None:
            if token is not self._active_commit_token and token != self._active_commit_token:
                raise StaleTokenError(
                    f"Active commit sequence bound to token for {self._active_commit_token}; "
                    f"rejected mismatched token {token}."
                )
            return self._active_commit_token

        if not hasattr(token, "state_version"):
            raise StaleTokenError(f"Invalid token type {type(token).__name__}: missing state_version.")

        if token.state_version != self.state_version:
            raise StaleTokenError(
                f"Stale OPT token rejected: token version {token.state_version} "
                f"does not match current OPT version {self.state_version}."
            )

        if token.current_step != self.current_step:
            raise StaleTokenError(
                f"Stale OPT token rejected: token step {token.current_step} "
                f"does not match current step {self.current_step}."
            )

        if isinstance(token, OptVictimToken):
            if token.victim_page not in self.cache:
                raise StaleTokenError(
                    f"Stale OptVictimToken rejected: victim page {token.victim_page} is no longer resident."
                )
            if self.page_to_frame.get(token.victim_page) != token.victim_frame_id:
                raise StaleTokenError(
                    f"Stale OptVictimToken rejected: victim page {token.victim_page} frame mapping changed."
                )
            if tuple(self.cache.keys()) != token.resident_snapshot:
                raise StaleTokenError(
                    f"Stale OptVictimToken rejected: resident cache contents or order changed since planning."
                )
            current_signature = tuple(
                (p, tuple(self.page_to_future_indices.get(p, ())))
                for p in token.resident_snapshot
            )
            if current_signature != token.future_indices_snapshot:
                raise StaleTokenError(
                    f"Stale OptVictimToken rejected: future reference indices changed since planning."
                )

        self._active_commit_token = token
        return token

    def evict_victim(self, victim: Any, token: Optional[Any] = None) -> None:
        """Phase 3 commit hook: Evict candidate victim page from resident state."""
        effective_token = self._validate_and_bind_token(token)
        v = self._normalize_page(victim) if self._is_integrated else victim
        if v in self.cache:
            del self.cache[v]
            if v in self.page_to_frame:
                del self.page_to_frame[v]
            self.replacements += 1

    def insert_page(
        self,
        incoming_page: Any,
        frame_id: Optional[int] = None,
        token: Optional[Any] = None
    ) -> None:
        """Phase 3 commit hook: Insert incoming page into resident state."""
        effective_token = self._validate_and_bind_token(token)
        p = self._normalize_page(incoming_page) if self._is_integrated else incoming_page

        self.cache[p] = self.current_step
        if frame_id is not None:
            self.page_to_frame[p] = frame_id

        if not self._is_integrated and token is None:
            # Standalone untokenized advances current_step in insert_page
            self.current_step += 1

    def record_hit(self, incoming_page: Any, token: Optional[Any] = None) -> None:
        """Phase 3 commit hook: Record a cache hit without altering arrival order."""
        effective_token = self._validate_and_bind_token(token)

    def commit_request(self, token: Optional[Any] = None, is_hit: bool = False) -> None:
        """Finalize transaction commit: advances sequence step, updates queues, and clears active session."""
        effective_token = token if token is not None else self._pending_token

        if self._active_commit_token is not None:
            if token is not None and token != self._active_commit_token:
                raise StaleTokenError(
                    f"Mismatched token in commit_request: expected {self._active_commit_token}, got {token}"
                )

        # Consume current step from expected page's future occurrences queue
        if self.sequence and self.current_step < len(self.sequence):
            expected_page = self.sequence[self.current_step]
            if expected_page in self.page_to_future_indices and self.page_to_future_indices[expected_page]:
                if self.page_to_future_indices[expected_page][0] == self.current_step:
                    self.page_to_future_indices[expected_page].popleft()

        self.current_step += 1
        self.requests += 1
        if is_hit:
            self.hits += 1
        else:
            self.misses += 1

        self.state_version += 1
        self._active_commit_token = None
        self._pending_token = None

    def abort_request(self, token: Optional[Any] = None) -> None:
        """Clear uncommitted plan and active commit session on transaction abort."""
        self._active_commit_token = None
        self._pending_token = None

    def purge_pid(self, pid: int) -> int:
        """
        Purge all resident pages belonging to the specified PID from the OPT cache.
        Non-process keys without a .pid attribute are safely ignored.
        Future occurrence deques and global sequence remain immutable.
        
        Raises:
            UnsupportedOperationError: If an integrated OPT transaction is active.
        """
        if self._pending_token is not None or self._active_commit_token is not None:
            raise UnsupportedOperationError(
                f"Cannot purge process {pid}: an integrated OPT transaction is active."
            )

        keys_to_purge = [k for k in self.cache if hasattr(k, "pid") and k.pid == pid]
        for k in keys_to_purge:
            del self.cache[k]
            if k in self.page_to_frame:
                del self.page_to_frame[k]

        if keys_to_purge:
            self.state_version += 1

        return len(keys_to_purge)

    def has_pending_references_for_pid(self, pid: int) -> bool:
        """
        Check whether any unconsumed future references in the remaining sequence belong to the specified PID.
        
        Inspects only the remaining future sequence from the current committed step index onward.
        Non-mutating: does not pop or alter future occurrence queues or sequence steps.
        
        Args:
            pid: Process ID to query.
            
        Returns:
            bool: True if at least one unconsumed future reference has page.pid == pid, False otherwise.
        """
        if not self.sequence or self.current_step >= len(self.sequence):
            return False
        return any(getattr(v, "pid", None) == pid for v in self.sequence[self.current_step:])

    def get_pending_references_for_pid(self, pid: int) -> List[Any]:
        """
        Return all unconsumed future references in the remaining sequence belonging to the specified PID.
        
        Inspects only the remaining future sequence from the current committed step index onward.
        Non-mutating: does not pop or alter future occurrence queues or sequence steps.
        
        Args:
            pid: Process ID to query.
            
        Returns:
            List of unconsumed reference items matching the specified PID.
        """
        if not self.sequence or self.current_step >= len(self.sequence):
            return []
        return [v for v in self.sequence[self.current_step:] if getattr(v, "pid", None) == pid]

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
            "state_version": self.state_version,
            "is_integrated": self._is_integrated,
            "algorithm": "OPT",
            "is_offline": True
        }

    def reset(self) -> None:
        """
        Reset standalone cache to pristine initial state.
        
        Raises:
            UnsupportedOperationError: If cache is integrated with MemoryManager.
        """
        if self._is_integrated:
            raise UnsupportedOperationError(
                "Direct call to OptimalCache.reset() is unsupported in integrated mode. "
                "Reset must be coordinated through MemoryManager.reset()."
            )
        self._reset_internal()

    def _coordinated_reset(self) -> None:
        """Coordinated reset path invoked exclusively by MemoryManager.reset()."""
        self._reset_internal()

    def _reset_internal(self) -> None:
        """Internal reset routine shared by standalone and coordinated reset."""
        self.cache.clear()
        self.page_to_frame.clear()
        self.requests = 0
        self.hits = 0
        self.misses = 0
        self.replacements = 0
        self._active_commit_token = None
        self._pending_token = None
        self.state_version += 1
        if self.sequence:
            self.initialize_sequence(list(self.sequence))
        else:
            self.current_step = 0


# Canonical Aliases
OPTCache = OptimalCache
