"""
CLOCK (Second-Chance) Page Replacement Algorithm.
Independent baseline implementation for comparative analysis.

Approximates Least Recently Used (LRU) using a circular buffer (clock hand)
and a reference/use bit per physical frame or slot.

On a page hit:
  The reference bit of the frame containing the page is set to 1.
  The clock hand does not advance.

On a page fault (miss with full cache):
  The clock hand sweeps across slots in circular order:
  - If reference bit == 1: Clears the bit to 0 (second chance granted)
    and advances the hand to (hand + 1) % capacity.
  - If reference bit == 0: Selects that slot as the victim, replaces the page,
    sets reference bit to 1, and advances the hand to (slot + 1) % capacity.
  In the worst case (all bits set to 1), every bit is cleared on the first
  revolution and the victim is chosen on the second revolution (guaranteed termination
  within at most 2 * capacity steps; no infinite loop).

This module is completely independent of FastAPI and GUI frameworks.
"""
from typing import Any, Dict, List, Optional


class CLOCKCache:
    """
    CLOCK / Second-Chance Cache Implementation.
    
    Data Structure Convention:
      - slots: List[Optional[Any]] of size capacity storing resident page keys.
      - reference_bits: List[int] of size capacity storing 0 or 1 reference flags.
      - page_to_slot: Dict[page_key, int] mapping resident page to its slot index for O(1) hit lookup.
      - hand: int pointer index indicating the next slot to be inspected (0 <= hand < capacity).
    """

    def __init__(self, capacity: int):
        """
        Initialize CLOCK Cache.
        
        Args:
            capacity: Maximum number of pages the cache can hold (integer >= 1).
        """
        if not isinstance(capacity, int) or capacity < 1:
            raise ValueError(f"Cache capacity must be a positive integer >= 1, got {capacity}")
            
        self.capacity: int = capacity
        
        # Circular array of physical frame slots
        self.slots: List[Optional[Any]] = [None] * capacity
        
        # Hardware-mirrored reference bits (0 or 1)
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

    @property
    def hit_ratio(self) -> float:
        """Calculate current cumulative hit ratio."""
        if self.requests == 0:
            return 0.0
        return round(self.hits / self.requests, 4)

    def contains(self, page: Any) -> bool:
        """Check if page is currently resident in cache."""
        return page in self.page_to_slot

    def is_full(self) -> bool:
        """Check if resident cache has reached maximum capacity."""
        return len(self.page_to_slot) >= self.capacity

    def get_reference(self, slot: int) -> int:
        """Get the reference bit for a specific slot."""
        if 0 <= slot < self.capacity:
            return self.reference_bits[slot]
        raise IndexError(f"Slot {slot} out of range [0, {self.capacity - 1}]")

    def set_reference(self, slot: int, bit: int) -> None:
        """Set the reference bit for a specific slot (0 or 1)."""
        if 0 <= slot < self.capacity:
            self.reference_bits[slot] = 1 if bit else 0
        else:
            raise IndexError(f"Slot {slot} out of range [0, {self.capacity - 1}]")

    def request(self, page: Any) -> Dict[str, Any]:
        """
        Process a page reference request according to the CLOCK second-chance policy.
        
        On a hit:
          - Increment hit counter.
          - Set reference bit of the resident page's slot to 1.
          - Hand pointer does not advance.
        On a miss:
          - Increment miss counter.
          - If empty slots exist, place page into the next free slot, set bit=1,
            and advance hand = (slot + 1) % capacity.
          - If full, sweep hand: clear bits of 1 to 0 and advance until a slot with
            bit 0 is found. Evict page in that slot, insert new page, set bit=1,
            increment replacements, and advance hand = (victim_slot + 1) % capacity.
            
        Args:
            page: Identifier of the requested page.
            
        Returns:
            Telemetry dictionary describing the access event and post-request state.
        """
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
                # Sweeps circularly at most 2 * capacity times, clearing bits until bit == 0 is encountered
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
                    # Invariant guarantee: after clearing all bits in round 1, round 2 must stop
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
        is_hit = incoming_page in self.page_to_slot
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
        Identify the candidate victim page without mutating resident state or hand pointer.
        Simulates the clock sweep on a temporary copy of reference bits.
        """
        if not self.page_to_slot or len(self.page_to_slot) < self.capacity:
            return None
            
        temp_hand = self.hand
        temp_bits = list(self.reference_bits)
        
        for _ in range(2 * self.capacity):
            if temp_bits[temp_hand] == 1:
                temp_bits[temp_hand] = 0
                temp_hand = (temp_hand + 1) % self.capacity
            else:
                return self.slots[temp_hand]
                
        return self.slots[self.hand]

    def evict_victim(self, victim: Any) -> None:
        """
        Confirm and execute eviction of the specified victim page.
        Sweeps the hand circularly, clearing reference bits of inspected frames until victim is reached.
        """
        if victim not in self.page_to_slot:
            return
            
        victim_slot = self.page_to_slot[victim]
        
        # Advance hand circularly and clear second-chance bits until reaching victim_slot
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

    def insert_page(self, incoming_page: Any, is_write: bool = False) -> None:
        """Insert incoming page into the next free slot or update existing."""
        if incoming_page in self.page_to_slot:
            slot = self.page_to_slot[incoming_page]
            self.reference_bits[slot] = 1
            return
            
        # Locate next empty slot starting from current hand position
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
        self.reference_bits[target_slot] = 1
        self.hand = (target_slot + 1) % self.capacity

    def get_state(self) -> Dict[str, Any]:
        """Capture current snapshot of the CLOCK cache state."""
        resident_pages = [p for p in self.slots if p is not None]
        return {
            "capacity": self.capacity,
            "size": len(self.page_to_slot),
            "size_cache": len(self.page_to_slot),
            "pages": resident_pages,
            "slots": list(self.slots),
            "reference_bits": list(self.reference_bits),
            "hand": self.hand,
            "requests": self.requests,
            "hits": self.hits,
            "misses": self.misses,
            "hit_ratio": self.hit_ratio,
            "replacements": self.replacements,
            "algorithm": "CLOCK"
        }

    def reset(self) -> None:
        """Reset the cache, circular buffer, and all counters to pristine initial state."""
        self.slots = [None] * self.capacity
        self.reference_bits = [0] * self.capacity
        self.page_to_slot.clear()
        self.hand = 0
        self.requests = 0
        self.hits = 0
        self.misses = 0
        self.replacements = 0

