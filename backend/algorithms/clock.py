"""
CLOCK (Second-Chance) Page Replacement Algorithm.
Faithful implementation supporting both standalone comparative analysis and
three-phase transactional OS MemoryManager integration.

Approximates Least Recently Used (LRU) using a circular buffer (clock hand)
and a reference/use bit per physical frame or slot.

In standalone mode:
  Maintains internal slots, reference_bits array, and hand pointer.

In integrated mode:
  PhysicalFrame is the authoritative owner of reference bits.
  CLOCK performs non-mutating simulated sweeps during planning (Phase 1)
  and produces immutable ClockVictimTokens. Reference-bit clearing and hand
  advancement are applied authoritatively during commit (Phase 3).
"""
from dataclasses import dataclass
from typing import Any, Callable, Dict, List, Optional, Set, Tuple

from backend.memory.exceptions import (
    BindingError,
    StaleTokenError,
    UnsupportedOperationError,
)


@dataclass(frozen=True)
class ClockVictimToken:
    """
    Immutable victim selection token produced by CLOCKCache during read-only planning.
    
    Contains all computed decisions needed for two-phase commit:
      - victim_page: The virtual page selected for eviction.
      - victim_frame_id: The physical frame slot of the victim.
      - hand_before: Clock hand position at the start of the sweep.
      - hand_after: Clock hand position after replacing the victim.
      - cleared_frames: Tuple of frame IDs whose reference bits must be cleared.
      - state_version: Monotonically increasing cache version at plan creation.
      - frame_ref_snapshot: Exact snapshot of allocated frame reference bits at plan creation.
    """
    victim_page: Any
    victim_frame_id: int
    hand_before: int
    hand_after: int
    cleared_frames: Tuple[int, ...]
    state_version: int
    frame_ref_snapshot: Tuple[Tuple[int, bool], ...]

    def __eq__(self, other: Any) -> bool:
        if isinstance(other, ClockVictimToken):
            return (
                self.victim_page == other.victim_page
                and self.victim_frame_id == other.victim_frame_id
                and self.hand_before == other.hand_before
                and self.hand_after == other.hand_after
                and self.cleared_frames == other.cleared_frames
                and self.state_version == other.state_version
                and self.frame_ref_snapshot == other.frame_ref_snapshot
            )
        return self.victim_page == other


@dataclass(frozen=True)
class ClockHitToken:
    """Immutable token produced for a resident page hit."""
    page: Any
    frame_id: int
    state_version: int
    status: str = "HIT"
    requires_eviction: bool = False

    def __getitem__(self, item: str) -> Any:
        if item == "status":
            return self.status
        if item == "requires_eviction":
            return self.requires_eviction
        raise KeyError(item)


@dataclass(frozen=True)
class ClockWarmupToken:
    """Immutable token produced for a cold miss when free physical frames exist."""
    page: Any
    state_version: int
    status: str = "COLD_MISS"
    requires_eviction: bool = False

    def __getitem__(self, item: str) -> Any:
        if item == "status":
            return self.status
        if item == "requires_eviction":
            return self.requires_eviction
        raise KeyError(item)


class CLOCKCache:
    """
    CLOCK / Second-Chance Cache Implementation.
    
    Data Structure Convention:
      - slots: List[Optional[Any]] of size capacity storing resident page keys.
      - reference_bits: List[int] of size capacity storing 0 or 1 reference flags (standalone only).
      - page_to_slot: Dict[page_key, int] mapping resident page to its slot index for O(1) hit lookup.
      - hand: int pointer index indicating the next slot to be inspected (0 <= hand < capacity).
      - state_version: Monotonically increasing version tracking authoritative state changes.
      - _is_integrated: Boolean flag enforcing one-way MemoryManager binding.
    """

    def __init__(self, capacity: int):
        """
        Initialize CLOCK Cache.
        
        Args:
            capacity: Maximum number of pages the cache can hold (integer >= 1).
        """
        if not isinstance(capacity, int) or isinstance(capacity, bool) or capacity < 1:
            raise ValueError(f"Cache capacity must be a positive integer >= 1, got {capacity!r}")
            
        self.capacity: int = capacity
        
        # Circular array of physical frame slots
        self.slots: List[Optional[Any]] = [None] * capacity
        
        # Standalone reference bits (0 or 1) - superseded by PhysicalFrame in integrated mode
        self.reference_bits: List[int] = [0] * capacity
        
        # Fast lookup mapping: page_identifier -> slot_index
        self.page_to_slot: Dict[Any, int] = {}
        
        # Clock hand pointer
        self.hand: int = 0
        
        # Performance counters
        self.requests: int = 0
        self.hits: int = 0
        self.misses: int = 0
        self.replacements: int = 0
        
        # Versioning & Integration State
        self.state_version: int = 0
        self._is_integrated: bool = False
        
        # Hardware accessor callbacks provided upon binding
        self._get_frame_ref_bit: Optional[Callable[[int], bool]] = None
        self._set_frame_ref_bit: Optional[Callable[[int, bool], None]] = None
        self._is_frame_allocated: Optional[Callable[[int], bool]] = None
        
        # Transaction commit session tokens
        self._active_commit_token: Optional[Any] = None
        self._pending_token: Optional[Any] = None

    def bind_to_manager(
        self,
        get_frame_ref_bit: Optional[Callable[[int], bool]] = None,
        set_frame_ref_bit: Optional[Callable[[int, bool], None]] = None,
        is_frame_allocated: Optional[Callable[[int], bool]] = None,
    ) -> None:
        """
        Bind this CLOCKCache instance to MemoryManager integrated mode.
        Enforces a one-way transition from a pristine standalone instance to integrated mode.
        
        Raises:
            BindingError: If cache is already integrated or not pristine.
        """
        if self._is_integrated:
            raise BindingError("CLOCKCache instance is already bound to MemoryManager.")
        if (
            self.requests > 0
            or self.hits > 0
            or self.misses > 0
            or self.replacements > 0
            or len(self.page_to_slot) > 0
            or any(s is not None for s in self.slots)
            or any(b != 0 for b in self.reference_bits)
            or self.hand != 0
        ):
            raise BindingError("Cannot bind a non-pristine CLOCKCache instance to MemoryManager.")

        self._is_integrated = True
        self._get_frame_ref_bit = get_frame_ref_bit
        self._set_frame_ref_bit = set_frame_ref_bit
        self._is_frame_allocated = is_frame_allocated

    @property
    def hit_ratio(self) -> float:
        """Calculate current cumulative hit ratio."""
        if self.requests == 0:
            return 0.0
        return round(self.hits / self.requests, 4)

    @property
    def size(self) -> int:
        """Current number of cached pages."""
        return len(self.page_to_slot)

    def contains(self, page: Any) -> bool:
        """Check if page is currently resident in cache."""
        return page in self.page_to_slot

    def is_full(self) -> bool:
        """Check if resident cache has reached maximum capacity."""
        return len(self.page_to_slot) >= self.capacity

    def get_reference(self, slot: int) -> int:
        """
        Get the reference bit for a specific slot.
        In integrated mode, reads directly from authoritative PhysicalFrame.
        """
        if not (0 <= slot < self.capacity):
            raise IndexError(f"Slot {slot} out of range [0, {self.capacity - 1}]")
        if self._is_integrated and self._get_frame_ref_bit is not None:
            return 1 if self._get_frame_ref_bit(slot) else 0
        return self.reference_bits[slot]

    def set_reference(self, slot: int, bit: int) -> None:
        """
        Set the reference bit for a specific slot (standalone only).
        
        Raises:
            UnsupportedOperationError: In integrated mode, where PhysicalFrame is authoritative.
        """
        if not (0 <= slot < self.capacity):
            raise IndexError(f"Slot {slot} out of range [0, {self.capacity - 1}]")
        if self._is_integrated:
            raise UnsupportedOperationError(
                "Direct reference-bit modification is unsupported in integrated mode. "
                "Use MemoryManager.set_frame_reference_bit()."
            )
        self.reference_bits[slot] = 1 if bit else 0

    def notify_frame_bit_changed(self, frame_id: int) -> None:
        """
        Callback from MemoryManager when a PhysicalFrame reference bit changes externally.
        Advances state_version and invalidates pending tokens.
        """
        if self._active_commit_token is not None:
            # Internal mutation during active commit sequence: do not self-invalidate
            return
        self.state_version += 1
        self._pending_token = None

    def request(self, page: Any) -> Dict[str, Any]:
        """
        Process a page reference request according to the CLOCK second-chance policy (standalone only).
        
        Raises:
            UnsupportedOperationError: If cache is integrated with MemoryManager.
        """
        if self._is_integrated:
            raise UnsupportedOperationError(
                "Direct call to CLOCKCache.request() is unsupported in integrated mode. "
                "All memory accesses must be coordinated through MemoryManager.access_page()."
            )

        self.requests += 1
        evicted_page = None
        
        if page in self.page_to_slot:
            # Page Hit
            self.hits += 1
            hit = True
            hit_type = "HIT"
            slot = self.page_to_slot[page]
            self.reference_bits[slot] = 1
            movement = f"Page {page} hit at slot {slot}; reference bit set to 1; hand remains at {self.hand}"
        else:
            # Page Miss (Fault)
            self.misses += 1
            hit = False
            hit_type = "MISS"
            
            if len(self.page_to_slot) < self.capacity:
                # Cache Warmup: Find the next available empty slot starting from hand
                target_slot = None
                for offset in range(self.capacity):
                    candidate = (self.hand + offset) % self.capacity
                    if self.slots[candidate] is None:
                        target_slot = candidate
                        break
                
                if target_slot is None:
                    target_slot = self.hand  # Fallback
                    
                self.slots[target_slot] = page
                self.page_to_slot[page] = target_slot
                self.reference_bits[target_slot] = 1
                self.hand = (target_slot + 1) % self.capacity
                movement = (
                    f"Cache warmup; loaded page {page} into empty slot {target_slot} (ref=1); "
                    f"hand advanced to {self.hand}"
                )
            else:
                # Cache Full: Second-Chance Clock Sweep
                victim_slot = None
                inspected_slots = []
                
                for _ in range(2 * self.capacity):
                    curr = self.hand
                    inspected_slots.append(curr)
                    if self.reference_bits[curr] == 1:
                        # Give second chance: clear bit and advance hand
                        self.reference_bits[curr] = 0
                        self.hand = (self.hand + 1) % self.capacity
                    else:
                        # Found victim with bit == 0
                        victim_slot = curr
                        break
                        
                if victim_slot is None:
                    victim_slot = self.hand
                    
                evicted_page = self.slots[victim_slot]
                del self.page_to_slot[evicted_page]
                
                # Replace with incoming page
                self.slots[victim_slot] = page
                self.page_to_slot[page] = victim_slot
                self.reference_bits[victim_slot] = 1
                self.replacements += 1
                
                # Advance hand past the replaced slot
                self.hand = (victim_slot + 1) % self.capacity
                
                movement = (
                    f"Cache full; swept slots {inspected_slots}; evicted page {evicted_page} "
                    f"from slot {victim_slot}; inserted {page} (ref=1); hand advanced to {self.hand}"
                )

        # Invariant Verification
        assert len(self.page_to_slot) <= self.capacity, (
            f"Invariant Violated: CLOCK cache size {len(self.page_to_slot)} exceeds capacity {self.capacity}"
        )
        assert 0 <= self.hand < self.capacity, (
            f"Invariant Violated: CLOCK hand {self.hand} out of bounds [0, {self.capacity - 1}]"
        )
        self.state_version += 1

        return {
            "page": page,
            "hit": hit,
            "hit_type": hit_type,
            "evicted_page": evicted_page,
            "movement": movement,
            "state": self.get_state()
        }

    def reset(self) -> None:
        """
        Reset standalone cache to pristine initial state.
        
        Raises:
            UnsupportedOperationError: If cache is integrated with MemoryManager.
        """
        if self._is_integrated:
            raise UnsupportedOperationError(
                "Direct call to CLOCKCache.reset() is unsupported in integrated mode. "
                "Reset must be coordinated through MemoryManager.reset()."
            )
        self._reset_internal()

    def _coordinated_reset(self) -> None:
        """
        Coordinated reset path invoked exclusively by MemoryManager.reset().
        Preserves integrated mode and monotonically advances state_version to invalidate prior tokens.
        """
        self._reset_internal()

    def _reset_internal(self) -> None:
        """Internal reset implementation shared by standalone and coordinated reset."""
        self.slots = [None] * self.capacity
        self.reference_bits = [0] * self.capacity
        self.page_to_slot.clear()
        self.hand = 0
        self.requests = 0
        self.hits = 0
        self.misses = 0
        self.replacements = 0
        self._active_commit_token = None
        self._pending_token = None
        # Monotonically advance state_version across reset to invalidate pre-reset tokens
        self.state_version += 1

    # -------------------------------------------------------------------------
    # Decoupled Policy Interface Contract (for MemoryManager Integration)
    # -------------------------------------------------------------------------

    def prepare_request(self, incoming_page: Any, process_context: Optional[int] = None) -> Any:
        """Classify incoming request and create validation token without mutating state."""
        if incoming_page in self.page_to_slot:
            token = ClockHitToken(
                page=incoming_page,
                frame_id=self.page_to_slot[incoming_page],
                state_version=self.state_version,
            )
        else:
            token = ClockWarmupToken(
                page=incoming_page,
                state_version=self.state_version,
            )
        if self._is_integrated:
            self._pending_token = token
        return token

    def requires_eviction(self, page: Optional[Any] = None, token: Optional[Any] = None) -> bool:
        """Check whether CLOCK requires eviction for the given access."""
        if page is not None and page in self.page_to_slot:
            return False
        return len(self.page_to_slot) >= self.capacity

    def select_victim(
        self,
        get_frame_ref_bit: Optional[Callable[[int], bool]] = None,
        token: Optional[Any] = None,
        is_eligible_cb: Optional[Callable[[Any], bool]] = None
    ) -> Optional[ClockVictimToken]:
        """
        Identify candidate victim frame and page without mutating CLOCK state or physical frames.
        Performs non-mutating simulated clock sweep starting from current hand.
        
        Free/unallocated frames are never considered as victims.
        If is_eligible_cb is provided, candidates that fail the predicate are skipped without
        having their reference bits cleared.
        """
        if not self.page_to_slot:
            return None

        # Determine reference bit reader
        ref_reader = get_frame_ref_bit or self._get_frame_ref_bit
        if ref_reader is None and not self._is_integrated:
            ref_reader = lambda s: bool(self.reference_bits[s])

        # Capture snapshot of current frame reference bits for validation
        ref_snapshot = []
        for fid in range(self.capacity):
            if self._is_integrated and self._is_frame_allocated is not None:
                is_alloc = self._is_frame_allocated(fid)
            else:
                is_alloc = (self.slots[fid] is not None)
            
            if is_alloc:
                ref_val = ref_reader(fid) if ref_reader else False
                ref_snapshot.append((fid, bool(ref_val)))

        sim_hand = self.hand
        cleared_frames: List[int] = []
        sim_cleared: Set[int] = set()
        victim_fid: Optional[int] = None

        # Sweep circularly at most 2 * capacity steps
        for _ in range(2 * self.capacity):
            # Free frames must not be considered as CLOCK victims
            if self._is_integrated and self._is_frame_allocated is not None:
                is_alloc = self._is_frame_allocated(sim_hand)
            else:
                is_alloc = (self.slots[sim_hand] is not None)

            if not is_alloc:
                sim_hand = (sim_hand + 1) % self.capacity
                continue

            page = self.slots[sim_hand]
            # Check eligibility before reference bit inspection
            if is_eligible_cb is not None and not is_eligible_cb(page):
                # Ineligible/protected page: skip without clearing its reference bit
                sim_hand = (sim_hand + 1) % self.capacity
                continue

            # Read reference bit (checking if already simulated as cleared)
            if sim_hand in sim_cleared:
                ref_bit = False
            else:
                ref_bit = bool(ref_reader(sim_hand)) if ref_reader else False

            if ref_bit:
                # Second chance: clear bit in simulation and advance hand
                cleared_frames.append(sim_hand)
                sim_cleared.add(sim_hand)
                sim_hand = (sim_hand + 1) % self.capacity
            else:
                # Found victim with reference bit == 0
                victim_fid = sim_hand
                break

        if victim_fid is None:
            # Fallback if all allocated frames were swept
            for offset in range(self.capacity):
                cand = (sim_hand + offset) % self.capacity
                if self.slots[cand] is not None:
                    if is_eligible_cb is None or is_eligible_cb(self.slots[cand]):
                        victim_fid = cand
                        break

        if victim_fid is None or self.slots[victim_fid] is None:
            return None

        victim_page = self.slots[victim_fid]
        hand_after = (victim_fid + 1) % self.capacity

        victim_token = ClockVictimToken(
            victim_page=victim_page,
            victim_frame_id=victim_fid,
            hand_before=self.hand,
            hand_after=hand_after,
            cleared_frames=tuple(cleared_frames),
            state_version=self.state_version,
            frame_ref_snapshot=tuple(ref_snapshot),
        )
        self._pending_token = victim_token
        return victim_token

    def _validate_and_bind_token(self, token: Optional[Any]) -> Any:
        """
        Validate token against current state_version, hand position, and frame reference bits.
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
                raise StaleTokenError("A valid Clock token is required for commit.")

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
                f"Stale CLOCK token rejected: token version {token.state_version} "
                f"does not match current CLOCK version {self.state_version}."
            )

        if isinstance(token, ClockVictimToken):
            if token.hand_before != self.hand:
                raise StaleTokenError(
                    f"Stale ClockVictimToken rejected: hand moved from {token.hand_before} to {self.hand}."
                )
            if self.slots[token.victim_frame_id] != token.victim_page:
                raise StaleTokenError(
                    f"Stale ClockVictimToken rejected: slot {token.victim_frame_id} no longer contains "
                    f"victim page {token.victim_page} (found {self.slots[token.victim_frame_id]})."
                )
            # Verify reference bits have not changed since planning
            ref_reader = self._get_frame_ref_bit
            if ref_reader is not None:
                for fid, expected_ref in token.frame_ref_snapshot:
                    curr_ref = bool(ref_reader(fid))
                    if curr_ref != expected_ref:
                        raise StaleTokenError(
                            f"Stale ClockVictimToken rejected: frame {fid} reference bit changed "
                            f"from {expected_ref} to {curr_ref} since planning."
                        )

        # Bind active commit session
        self._active_commit_token = token
        return token

    def evict_victim(self, victim: Any, token: Optional[Any] = None) -> None:
        """
        Phase 3 commit hook: Evict candidate victim frame.
        Applies planned second-chance reference-bit clearing to physical frames,
        clears victim slot, and updates clock hand.
        """
        effective_token = self._validate_and_bind_token(token)

        if isinstance(effective_token, ClockVictimToken):
            victim_slot = effective_token.victim_frame_id
            
            # Apply planned second-chance clearing to physical frames via MemoryManager callback
            if self._is_integrated and self._set_frame_ref_bit is not None:
                for fid in effective_token.cleared_frames:
                    self._set_frame_ref_bit(fid, False)
            elif not self._is_integrated:
                for fid in effective_token.cleared_frames:
                    self.reference_bits[fid] = 0

            # Clear victim residency
            self.slots[victim_slot] = None
            if victim in self.page_to_slot:
                del self.page_to_slot[victim]
            self.replacements += 1

            # Update hand pointer to slot after victim
            self.hand = effective_token.hand_after
        else:
            # Standalone untokenized eviction (backward-compatible)
            if victim in self.page_to_slot:
                victim_slot = self.page_to_slot[victim]
                for _ in range(2 * self.capacity):
                    if self.reference_bits[self.hand] == 1:
                        self.reference_bits[self.hand] = 0
                        self.hand = (self.hand + 1) % self.capacity
                    elif self.hand == victim_slot:
                        break
                    else:
                        self.hand = (self.hand + 1) % self.capacity

                self.slots[victim_slot] = None
                self.reference_bits[victim_slot] = 0
                del self.page_to_slot[victim]
                self.replacements += 1
                self.hand = (victim_slot + 1) % self.capacity

    def insert_page(
        self,
        incoming_page: Any,
        frame_id: Optional[int] = None,
        token: Optional[Any] = None
    ) -> None:
        """Phase 3 commit hook: Insert incoming page into allocated physical frame slot."""
        effective_token = self._validate_and_bind_token(token)

        if frame_id is not None:
            target_slot = frame_id
        else:
            target_slot = None
            for offset in range(self.capacity):
                candidate = (self.hand + offset) % self.capacity
                if self.slots[candidate] is None:
                    target_slot = candidate
                    break
            if target_slot is None:
                target_slot = self.hand

        self.slots[target_slot] = incoming_page
        self.page_to_slot[incoming_page] = target_slot

        if not self._is_integrated:
            self.reference_bits[target_slot] = 1
            self.hand = (target_slot + 1) % self.capacity
        elif isinstance(effective_token, ClockWarmupToken):
            # Hand advances past the loaded frame on warmup
            self.hand = (target_slot + 1) % self.capacity

        self.requests += 1
        self.misses += 1

    def record_hit(self, vpage: Any, token: Optional[Any] = None) -> None:
        """Phase 3 commit hook: Update policy state upon a resident page hit."""
        effective_token = self._validate_and_bind_token(token)

        if not self._is_integrated and vpage in self.page_to_slot:
            slot = self.page_to_slot[vpage]
            self.reference_bits[slot] = 1

        self.requests += 1
        self.hits += 1

    def commit_request(self, token: Optional[Any] = None, is_hit: bool = False) -> None:
        """Finalize transaction commit: advances state_version monotonically and clears active session."""
        if self._active_commit_token is not None:
            if token is not None and token != self._active_commit_token:
                raise StaleTokenError(
                    f"Mismatched token in commit_request: expected {self._active_commit_token}, got {token}"
                )
            self.state_version += 1
            self._active_commit_token = None
        elif token is not None:
            self.state_version += 1
        self._pending_token = None

    def abort_request(self, token: Optional[Any] = None) -> None:
        """Clear uncommitted plan and active commit session on transaction abort."""
        self._active_commit_token = None
        self._pending_token = None

    def purge_pid(self, pid: int) -> int:
        """
        Purge all pages belonging to the specified PID from the CLOCK cache (standalone only).
        Non-process keys without a .pid attribute are safely ignored.
        
        Raises:
            UnsupportedOperationError: If cache is integrated with MemoryManager, or if an
                                      integrated CLOCK transaction is active.
        """
        if self._pending_token is not None or self._active_commit_token is not None:
            raise UnsupportedOperationError(
                f"Cannot purge process {pid}: an integrated CLOCK transaction is active."
            )

        if self._is_integrated:
            raise UnsupportedOperationError(
                "Direct call to CLOCKCache.purge_pid() is unsupported in integrated mode. "
                "Integrated CLOCK process teardown must be coordinated through MemoryManager."
            )

        purged_count = 0
        for slot in range(self.capacity):
            page = self.slots[slot]
            if page is not None and hasattr(page, "pid") and page.pid == pid:
                self.slots[slot] = None
                if page in self.page_to_slot:
                    del self.page_to_slot[page]
                self.reference_bits[slot] = 0
                purged_count += 1

        if purged_count > 0:
            self.state_version += 1

        return purged_count

    def get_state(self) -> Dict[str, Any]:
        """Capture current snapshot of the CLOCK cache state."""
        resident_pages = [p for p in self.slots if p is not None]
        if self._is_integrated and self._get_frame_ref_bit is not None:
            ref_bits = [1 if self._get_frame_ref_bit(i) else 0 for i in range(self.capacity)]
        else:
            ref_bits = list(self.reference_bits)

        return {
            "capacity": self.capacity,
            "size": len(self.page_to_slot),
            "size_cache": len(self.page_to_slot),
            "pages": resident_pages,
            "slots": list(self.slots),
            "reference_bits": ref_bits,
            "hand": self.hand,
            "requests": self.requests,
            "hits": self.hits,
            "misses": self.misses,
            "hit_ratio": self.hit_ratio,
            "replacements": self.replacements,
            "state_version": self.state_version,
            "is_integrated": self._is_integrated,
            "algorithm": "CLOCK"
        }
