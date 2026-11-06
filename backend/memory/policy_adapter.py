"""
Policy Adapter bridging standalone replacement algorithms with MemoryManager lifecycle.
"""
from collections import OrderedDict
from dataclasses import dataclass
from typing import Any, Callable, Dict, Optional, Sequence, Set

from backend.algorithms.arc import ARCCache
from backend.algorithms.clock import CLOCKCache, ClockVictimToken
from backend.algorithms.fifo import FIFOCache
from backend.algorithms.lfu import LFUCache
from backend.algorithms.lru import LRUCache
from backend.algorithms.mru import MRUCache
from backend.algorithms.opt import OptimalCache, OptVictimToken
from backend.memory.exceptions import (
    BindingError,
    UnsupportedOperationError,
    UnsupportedPolicyError,
)
from backend.memory.page import VirtualPage

SUPPORTED_POLICIES = {"LRU", "FIFO", "LFU", "MRU", "ARC", "CLOCK", "OPT", "OPTIMAL"}
UNSUPPORTED_POLICIES: Dict[str, str] = {}


@dataclass(frozen=True)
class VictimSelectionResult:
    """Standardized manager-facing victim selection result."""
    victim: VirtualPage
    frame_id: Optional[int]
    token: Optional[Any] = None
    eviction_path: Optional[str] = None

    @property
    def victim_page(self) -> VirtualPage:
        return self.victim

    @property
    def victim_frame_id(self) -> Optional[int]:
        return self.frame_id


@dataclass(frozen=True, init=False)
class ClockVictimSelectionResult(ClockVictimToken, VictimSelectionResult):
    """CLOCK victim selection result inheriting from both ClockVictimToken and VictimSelectionResult."""
    def __init__(self, token: ClockVictimToken):
        object.__setattr__(self, "victim", token.victim_page)
        object.__setattr__(self, "frame_id", token.victim_frame_id)
        object.__setattr__(self, "token", token)
        object.__setattr__(self, "eviction_path", "CLOCK_SWEEP")
        object.__setattr__(self, "hand_before", token.hand_before)
        object.__setattr__(self, "hand_after", token.hand_after)
        object.__setattr__(self, "cleared_frames", token.cleared_frames)
        object.__setattr__(self, "state_version", token.state_version)
        object.__setattr__(self, "frame_ref_snapshot", token.frame_ref_snapshot)


@dataclass(frozen=True, init=False)
class OptVictimSelectionResult(OptVictimToken, VictimSelectionResult):
    """OPT victim selection result inheriting from both OptVictimToken and VictimSelectionResult."""
    def __init__(self, token: OptVictimToken):
        object.__setattr__(self, "victim", token.victim_page)
        object.__setattr__(self, "frame_id", token.victim_frame_id)
        object.__setattr__(self, "token", token)
        object.__setattr__(self, "eviction_path", "OPT_FURTHEST_FUTURE")
        object.__setattr__(self, "current_step", token.current_step)
        object.__setattr__(self, "state_version", token.state_version)
        object.__setattr__(self, "future_distance", token.future_distance)
        object.__setattr__(self, "resident_snapshot", token.resident_snapshot)
        object.__setattr__(self, "expected_page", token.expected_page)
        object.__setattr__(self, "future_indices_snapshot", token.future_indices_snapshot)


class PolicyAdapter:
    """
    Adapter bridging replacement algorithms with MemoryManager's 3-phase transaction lifecycle.
    
    Provides standardized methods:
      - validate_request(vpage) -> Optional[Any]
      - select_victim(get_frame_ref_bit) -> Any
      - evict_victim(victim, token) -> None
      - insert_page(vpage, frame_id, token) -> None
      - record_hit(vpage, token) -> None
      - commit_request(token, is_hit) -> None
      - reset() -> None
    """

    def __init__(
        self,
        algorithm: Any,
        capacity: int,
        get_frame_ref_bit: Optional[Callable[[int], bool]] = None,
        set_frame_ref_bit: Optional[Callable[[int, bool], None]] = None,
        is_frame_allocated: Optional[Callable[[int], bool]] = None,
        reference_sequence: Optional[Sequence[Any]] = None,
    ):
        """
        Initialize adapter with a supported algorithm instance or algorithm name.
        
        Args:
            algorithm: Algorithm instance or name string.
            capacity: Physical frame capacity.
            get_frame_ref_bit: Optional accessor for physical frame reference bit.
            set_frame_ref_bit: Optional mutator for physical frame reference bit.
            is_frame_allocated: Optional check for physical frame occupancy.
            reference_sequence: Optional future reference sequence for offline policies (OPT).
            
        Raises:
            UnsupportedPolicyError: If algorithm is unknown.
        """
        self.capacity: int = capacity
        self._get_frame_ref_bit = get_frame_ref_bit
        self._set_frame_ref_bit = set_frame_ref_bit
        self._is_frame_allocated = is_frame_allocated
        
        if isinstance(algorithm, str):
            name = algorithm.upper()
            if name in UNSUPPORTED_POLICIES:
                raise UnsupportedPolicyError(
                    f"Policy '{name}' is currently unsupported in MemoryManager: {UNSUPPORTED_POLICIES[name]}"
                )
            if name == "LRU":
                self.policy = LRUCache(capacity)
            elif name == "FIFO":
                self.policy = FIFOCache(capacity)
            elif name == "LFU":
                self.policy = LFUCache(capacity)
            elif name == "MRU":
                self.policy = MRUCache(capacity)
            elif name == "ARC":
                self.policy = ARCCache(capacity)
                self.policy.bind_to_manager()
            elif name == "CLOCK":
                self.policy = CLOCKCache(capacity)
                self.policy.bind_to_manager(
                    get_frame_ref_bit=get_frame_ref_bit,
                    set_frame_ref_bit=set_frame_ref_bit,
                    is_frame_allocated=is_frame_allocated,
                )
            elif name in ("OPT", "OPTIMAL"):
                if reference_sequence is None:
                    raise ValueError(
                        "OPT replacement policy requires a reference_sequence to be provided."
                    )
                self.policy = OptimalCache(capacity, reference_sequence=reference_sequence)
                self.policy.bind_to_manager(reference_sequence=reference_sequence)
            else:
                raise UnsupportedPolicyError(
                    f"Unknown replacement algorithm name: {algorithm!r}. "
                    f"Currently supported policies: {', '.join(sorted(SUPPORTED_POLICIES))}."
                )
        else:
            if not isinstance(algorithm, (LRUCache, FIFOCache, LFUCache, MRUCache, ARCCache, CLOCKCache, OptimalCache)):
                raise UnsupportedPolicyError(
                    f"Unsupported policy instance type {type(algorithm).__name__}. "
                    f"Currently supported policies: {', '.join(sorted(SUPPORTED_POLICIES))}."
                )
            if isinstance(algorithm, ARCCache):
                algorithm.bind_to_manager()
            elif isinstance(algorithm, CLOCKCache):
                algorithm.bind_to_manager(
                    get_frame_ref_bit=get_frame_ref_bit,
                    set_frame_ref_bit=set_frame_ref_bit,
                    is_frame_allocated=is_frame_allocated,
                )
            elif isinstance(algorithm, OptimalCache):
                algorithm.bind_to_manager(reference_sequence=reference_sequence)
            self.policy = algorithm

        self.name: str = getattr(self.policy, "__class__", type(self.policy)).__name__
        self._pending_token: Optional[Any] = None

    def validate_request(self, vpage: VirtualPage) -> Optional[Any]:
        """Read-only request validation hook."""
        if hasattr(self.policy, "prepare_request"):
            self._pending_token = self.policy.prepare_request(vpage)
            return self._pending_token
        if hasattr(self.policy, "validate_request"):
            self._pending_token = self.policy.validate_request(vpage)
            return self._pending_token
        self._pending_token = None
        return None

    def requires_eviction(self, vpage: Optional[VirtualPage] = None, token: Optional[Any] = None) -> bool:
        """
        Check whether policy requires an eviction for the current access.
        """
        effective_token = token if token is not None else self._pending_token
        if hasattr(self.policy, "requires_eviction"):
            return self.policy.requires_eviction(vpage, effective_token)
        if hasattr(self.policy, "cache"):
            return len(self.policy.cache) >= self.capacity
        if hasattr(self.policy, "page_to_freq"):
            return len(self.policy.page_to_freq) >= self.capacity
        if hasattr(self.policy, "page_to_slot"):
            return len(self.policy.page_to_slot) >= self.capacity
        return False

    def select_victim(
        self,
        get_frame_ref_bit: Optional[Callable[[int], bool]] = None,
        token: Optional[Any] = None,
        is_eligible_cb: Optional[Callable[[Any], bool]] = None,
    ) -> Optional[VictimSelectionResult]:
        """
        Identify candidate victim page without mutating resident state.
        
        Standardizes return across all seven algorithms into Optional[VictimSelectionResult].
        Returns None when no eligible victim exists.
        
        Args:
            get_frame_ref_bit: Frame reference bit accessor (for CLOCK).
            token: Active validation plan/token (for ARC, CLOCK, OPT).
            is_eligible_cb: Optional predicate callback returning True if a page is eligible.
            
        Returns:
            VictimSelectionResult with victim, frame_id, token, and eviction_path,
            or None if no candidate is eligible.
        """
        effective_token = token if token is not None else self._pending_token

        # 1. ARC Cache
        if isinstance(self.policy, ARCCache):
            arc_victim = self.policy.select_victim(plan=effective_token, is_eligible_cb=is_eligible_cb)
            if arc_victim is None:
                return None
            plan = self.policy._pending_plan if self.policy._pending_plan is not None else effective_token
            eviction_path = plan.eviction_type if (plan is not None and getattr(plan, "eviction_type", None) is not None) else "ARC_REPLACE"
            return VictimSelectionResult(
                victim=arc_victim,
                frame_id=None,
                token=plan,
                eviction_path=eviction_path,
            )

        # 2. CLOCK Cache
        if isinstance(self.policy, CLOCKCache):
            ref_reader = get_frame_ref_bit or self._get_frame_ref_bit
            victim_token = self.policy.select_victim(ref_reader, token=effective_token, is_eligible_cb=is_eligible_cb)
            if victim_token is None:
                return None
            return ClockVictimSelectionResult(victim_token)

        # 3. OPT Cache
        if isinstance(self.policy, OptimalCache):
            opt_victim = self.policy.select_victim(token=effective_token, is_eligible_cb=is_eligible_cb)
            if opt_victim is None:
                return None
            if isinstance(opt_victim, OptVictimToken):
                return OptVictimSelectionResult(opt_victim)
            token = self.policy._pending_token if hasattr(self.policy, "_pending_token") else effective_token
            if isinstance(token, OptVictimToken):
                return OptVictimSelectionResult(token)
            return VictimSelectionResult(
                victim=opt_victim,
                frame_id=self.policy.page_to_frame.get(opt_victim, None) if hasattr(self.policy, "page_to_frame") else None,
                token=token,
                eviction_path="OPT_FURTHEST_FUTURE",
            )

        # 4. LRU Cache
        if isinstance(self.policy, LRUCache):
            victim = self.policy.select_victim(is_eligible_cb=is_eligible_cb)
            if victim is None:
                return None
            return VictimSelectionResult(
                victim=victim,
                frame_id=None,
                token=None,
                eviction_path=self.name,
            )

        # 5. FIFO Cache
        if isinstance(self.policy, FIFOCache):
            victim = self.policy.select_victim(is_eligible_cb=is_eligible_cb)
            if victim is None:
                return None
            return VictimSelectionResult(
                victim=victim,
                frame_id=None,
                token=None,
                eviction_path=self.name,
            )

        # 6. MRU Cache
        if isinstance(self.policy, MRUCache):
            victim = self.policy.select_victim(is_eligible_cb=is_eligible_cb)
            if victim is None:
                return None
            return VictimSelectionResult(
                victim=victim,
                frame_id=None,
                token=None,
                eviction_path=self.name,
            )

        # 7. LFU Cache
        if isinstance(self.policy, LFUCache):
            victim = self.policy.select_victim(is_eligible_cb=is_eligible_cb)
            if victim is None:
                return None
            return VictimSelectionResult(
                victim=victim,
                frame_id=None,
                token=None,
                eviction_path=self.name,
            )

        # Fallback for generic policies with select_victim
        if hasattr(self.policy, "select_victim"):
            try:
                victim = self.policy.select_victim(is_eligible_cb=is_eligible_cb)
            except TypeError:
                victim = self.policy.select_victim()
                if is_eligible_cb is not None and victim is not None and not is_eligible_cb(victim):
                    victim = None
            if victim is None:
                return None
            return VictimSelectionResult(
                victim=victim,
                frame_id=None,
                token=None,
                eviction_path=self.name,
            )

        raise UnsupportedOperationError(f"Policy {self.name} does not support select_victim()")

    def evict_victim(self, victim: Any, token: Optional[Any] = None) -> None:
        """Confirm and execute eviction of the specified victim page."""
        effective_token = token if token is not None else self._pending_token
        actual_victim = victim.victim if isinstance(victim, VictimSelectionResult) else victim

        if isinstance(self.policy, ARCCache):
            self.policy.evict_victim(actual_victim, effective_token)
            return

        if isinstance(self.policy, CLOCKCache):
            self.policy.evict_victim(actual_victim, effective_token)
            return

        if isinstance(self.policy, OptimalCache):
            self.policy.evict_victim(actual_victim, effective_token)
            return

        if hasattr(self.policy, "evict_victim"):
            self.policy.evict_victim(actual_victim)
            return

        if isinstance(self.policy, (LRUCache, FIFOCache)):
            if actual_victim in self.policy.cache:
                del self.policy.cache[actual_victim]
                self.policy.replacements += 1
            return

        raise UnsupportedOperationError(f"Policy {self.name} does not support evict_victim()")

    def purge_process(self, pid: int) -> int:
        """
        Delegate process purging to the underlying policy algorithm.
        
        Args:
            pid: Process ID whose pages should be purged.
            
        Returns:
            Count of pages purged by the policy.
            
        Raises:
            UnsupportedOperationError: If an active transaction token is pending,
                                      if the policy does not support purge_pid,
                                      or if direct purging is prohibited (integrated CLOCK).
        """
        if self._pending_token is not None:
            raise UnsupportedOperationError(
                f"Cannot purge process {pid}: PolicyAdapter has an active pending token."
            )
        if not hasattr(self.policy, "purge_pid"):
            raise UnsupportedOperationError(
                f"Policy {self.name} does not support process purging (missing purge_pid)."
            )
        return self.policy.purge_pid(pid)

    def insert_page(self, vpage: VirtualPage, frame_id: int, token: Optional[Any] = None) -> None:
        """Insert incoming page into policy residency structures."""
        effective_token = token if token is not None else self._pending_token

        if isinstance(self.policy, ARCCache):
            self.policy.insert_page(vpage, frame_id, effective_token)
            return

        if isinstance(self.policy, CLOCKCache):
            self.policy.insert_page(vpage, frame_id, effective_token)
            return

        if isinstance(self.policy, OptimalCache):
            self.policy.insert_page(vpage, frame_id, effective_token)
            return

        if hasattr(self.policy, "insert_page"):
            try:
                self.policy.insert_page(vpage, frame_id)
            except TypeError:
                self.policy.insert_page(vpage)
            return

        if isinstance(self.policy, LRUCache):
            self.policy.cache[vpage] = frame_id
            self.policy.cache.move_to_end(vpage, last=True)
            return

        if isinstance(self.policy, FIFOCache):
            self.policy.cache[vpage] = frame_id
            return

        raise UnsupportedOperationError(f"Policy {self.name} does not support insert_page()")

    def record_hit(self, vpage: VirtualPage, token: Optional[Any] = None) -> None:
        """Update policy state upon a resident page hit."""
        effective_token = token if token is not None else self._pending_token

        if isinstance(self.policy, ARCCache):
            self.policy.record_hit(vpage, effective_token)
            return

        if isinstance(self.policy, CLOCKCache):
            self.policy.record_hit(vpage, effective_token)
            return

        if isinstance(self.policy, OptimalCache):
            self.policy.record_hit(vpage, effective_token)
            return

        if hasattr(self.policy, "record_hit"):
            self.policy.record_hit(vpage)
            return

        if isinstance(self.policy, LRUCache):
            if vpage in self.policy.cache:
                self.policy.cache.move_to_end(vpage, last=True)
            return

        if isinstance(self.policy, FIFOCache):
            # FIFO does not alter order on hit
            return

        if isinstance(self.policy, LFUCache):
            if vpage in self.policy.page_to_freq:
                old_f = self.policy.page_to_freq[vpage]
                new_f = old_f + 1
                self.policy.page_to_freq[vpage] = new_f
                del self.policy.freq_to_pages[old_f][vpage]
                if not self.policy.freq_to_pages[old_f]:
                    del self.policy.freq_to_pages[old_f]
                    if self.policy.min_freq == old_f:
                        self.policy.min_freq = new_f
                self.policy.freq_to_pages.setdefault(new_f, OrderedDict())[vpage] = None
            return

        if isinstance(self.policy, MRUCache):
            if vpage in self.policy.cache:
                self.policy.cache.move_to_end(vpage, last=True)
            return

    def commit_request(self, token: Optional[Any] = None, is_hit: bool = False) -> None:
        """Commit request advancement (e.g. for OptimalCache or ARCCache)."""
        effective_token = token if token is not None else self._pending_token
        self._pending_token = None
        if hasattr(self.policy, "commit_request"):
            self.policy.commit_request(effective_token, is_hit)

    def abort_request(self, token: Optional[Any] = None) -> None:
        """Clear uncommitted plan or token on transaction abort."""
        effective_token = token if token is not None else self._pending_token
        self._pending_token = None
        if hasattr(self.policy, "abort_request"):
            try:
                self.policy.abort_request(effective_token)
            except TypeError:
                self.policy.abort_request()

    def contains(self, vpage: VirtualPage) -> bool:
        """Check if page is currently resident according to policy."""
        if hasattr(self.policy, "contains"):
            return self.policy.contains(vpage)
        if hasattr(self.policy, "cache"):
            return vpage in self.policy.cache
        if hasattr(self.policy, "page_to_freq"):
            return vpage in self.policy.page_to_freq
        if hasattr(self.policy, "page_to_slot"):
            return vpage in self.policy.page_to_slot
        return False

    def reset(self) -> None:
        """Reset policy structures to empty initial state."""
        self._pending_token = None
        if hasattr(self.policy, "_coordinated_reset"):
            self.policy._coordinated_reset()
        elif hasattr(self.policy, "reset"):
            self.policy.reset()

