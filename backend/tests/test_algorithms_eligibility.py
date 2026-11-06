"""
Focused unit and invariant tests for Stage 12.2 Phase 4C Substage 4C.1:
Algorithmic Eligibility Callbacks, Active Transaction Guards, and Purge Hooks.

Covers:
  1. Standardized victim-selection contract returning None (not a tuple) when no candidate is eligible.
  2. Baseline victim-selection order and tie-breaking preserved across all seven algorithms when is_eligible_cb is None.
  3. Non-mutating eligibility filtering for LRU, FIFO, MRU, and LFU.
  4. ARC quota-constrained primary and alternate list selection (T1 -> T2 and T2 -> T1 fallback).
  5. ARC target parameter p strictly unchanged during eligibility fallback.
  6. ARC victim entering the correct ghost list (B1 or B2) matching its actual resident source list.
  7. ARC Case 4A direct T1 drop vs ghost pruning with eligibility filtering.
  8. CLOCK simulated sweep skipping ineligible frames without clearing reference bits.
  9. CLOCK victim selection returning None when all candidate frames are ineligible.
  10. OPT victim selection returning None when all candidate resident pages are ineligible.
  11. OPT selecting the furthest future reference among eligible candidates.
  12. Process purge hooks (purge_pid) across all seven algorithms.
  13. Transaction preflight rejection during active plans/tokens in ARC, CLOCK, and OPT.
  14. Safe handling of non-process integer and string keys across all seven algorithms.
  15. ARC p adaptation preservation and [0, c] clamping during purge.
  16. OPT future sequence and index deque immutability during purge.
  17. LFU min_freq maintenance during purge.
"""
from dataclasses import replace
import pytest

from backend.algorithms.arc import ARCCache, ArcPlan
from backend.algorithms.clock import CLOCKCache, ClockVictimToken
from backend.algorithms.fifo import FIFOCache
from backend.algorithms.lfu import LFUCache
from backend.algorithms.lru import LRUCache
from backend.algorithms.mru import MRUCache
from backend.algorithms.opt import OPTCache, OptimalCache, OptVictimToken
from backend.memory.exceptions import StaleTokenError, UnsupportedOperationError
from backend.memory.manager import MemoryManager
from backend.memory.page import VirtualPage


# =============================================================================
# 1. LRU, FIFO, MRU, LFU Eligibility Filtering and Order Preservation
# =============================================================================

def test_lru_eligibility_filter_and_none():
    """LRU skips ineligible pages from head (LRU), selects first eligible, or returns None."""
    cache = LRUCache(3)
    p1 = VirtualPage(1, 10)
    p2 = VirtualPage(2, 20)
    p3 = VirtualPage(1, 30)

    cache.request(p1)
    cache.request(p2)
    cache.request(p3)
    # LRU order: p1 (oldest/LRU), p2, p3 (MRU)

    # All eligible: returns p1
    assert cache.select_victim(is_eligible_cb=None) == p1

    # Filter out p1: returns p2
    assert cache.select_victim(is_eligible_cb=lambda p: p != p1) == p2

    # Filter out p1 and p2: returns p3
    assert cache.select_victim(is_eligible_cb=lambda p: p == p3) == p3

    # All ineligible: returns None
    assert cache.select_victim(is_eligible_cb=lambda p: False) is None

    # Verify zero cache mutation occurred
    assert list(cache.cache.keys()) == [p1, p2, p3]


def test_fifo_eligibility_filter_and_none():
    """FIFO skips ineligible pages from head (oldest arrival), selects first eligible, or returns None."""
    cache = FIFOCache(3)
    p1 = VirtualPage(1, 10)
    p2 = VirtualPage(2, 20)
    p3 = VirtualPage(1, 30)

    cache.request(p1)
    cache.request(p2)
    cache.request(p3)
    # Arrival order: p1, p2, p3

    # All eligible: returns p1
    assert cache.select_victim(is_eligible_cb=None) == p1

    # Filter out p1: returns p2
    assert cache.select_victim(is_eligible_cb=lambda p: p.pid == 2) == p2

    # All ineligible: returns None
    assert cache.select_victim(is_eligible_cb=lambda p: False) is None

    # Verify zero cache mutation occurred
    assert list(cache.cache.keys()) == [p1, p2, p3]


def test_mru_eligibility_filter_and_none():
    """MRU skips ineligible pages from tail (MRU), selects most recent eligible, or returns None."""
    cache = MRUCache(3)
    p1 = VirtualPage(1, 10)
    p2 = VirtualPage(2, 20)
    p3 = VirtualPage(1, 30)

    cache.request(p1)
    cache.request(p2)
    cache.request(p3)
    # Order: p1, p2, p3 (MRU)

    # All eligible: returns p3 (MRU)
    assert cache.select_victim(is_eligible_cb=None) == p3

    # Filter out p3: returns p2
    assert cache.select_victim(is_eligible_cb=lambda p: p != p3) == p2

    # Filter out p2 and p3: returns p1
    assert cache.select_victim(is_eligible_cb=lambda p: p == p1) == p1

    # All ineligible: returns None
    assert cache.select_victim(is_eligible_cb=lambda p: False) is None

    # Verify zero cache mutation occurred
    assert list(cache.cache.keys()) == [p1, p2, p3]


def test_lfu_eligibility_filter_and_none():
    """LFU searches lowest frequency bucket containing an eligible candidate, or returns None."""
    cache = LFUCache(4)
    p1 = VirtualPage(1, 10)  # freq = 3
    p2 = VirtualPage(1, 20)  # freq = 2
    p3 = VirtualPage(2, 30)  # freq = 1
    p4 = VirtualPage(2, 40)  # freq = 1

    cache.request(p1)
    cache.request(p1)
    cache.request(p1)

    cache.request(p2)
    cache.request(p2)

    cache.request(p3)
    cache.request(p4)
    # Buckets: freq 1 -> [p3, p4], freq 2 -> [p2], freq 3 -> [p1]

    # Baseline: returns p3 (earliest arrival in min_freq bucket 1)
    assert cache.select_victim(is_eligible_cb=None) == p3

    # Filter out p3: selects p4 from freq 1
    assert cache.select_victim(is_eligible_cb=lambda p: p != p3) == p4

    # Filter out all freq 1 pages (p3, p4): selects p2 from freq 2
    assert cache.select_victim(is_eligible_cb=lambda p: p.pid == 1) == p2

    # Filter out freq 1 and freq 2: selects p1 from freq 3
    assert cache.select_victim(is_eligible_cb=lambda p: p == p1) == p1

    # All ineligible: returns None
    assert cache.select_victim(is_eligible_cb=lambda p: False) is None

    # Verify zero mutation
    assert len(cache.page_to_freq) == 4


def test_existing_victim_ordering_unchanged():
    """Baseline victim selection ordering and tie-breaking are preserved when is_eligible_cb is None."""
    # LRU
    lru = LRUCache(2)
    lru.request("a")
    lru.request("b")
    assert lru.select_victim() == "a"
    assert lru.select_victim(is_eligible_cb=lambda _: True) == "a"

    # FIFO
    fifo = FIFOCache(2)
    fifo.request("a")
    fifo.request("b")
    assert fifo.select_victim() == "a"
    assert fifo.select_victim(is_eligible_cb=lambda _: True) == "a"

    # MRU
    mru = MRUCache(2)
    mru.request("a")
    mru.request("b")
    assert mru.select_victim() == "b"
    assert mru.select_victim(is_eligible_cb=lambda _: True) == "b"

    # LFU
    lfu = LFUCache(2)
    lfu.request("a")
    lfu.request("b")
    assert lfu.select_victim() == "a"
    assert lfu.select_victim(is_eligible_cb=lambda _: True) == "a"


# =============================================================================
# 2. ARC Eligibility Filtering, Quota Fallback, and Direct T1 Drop
# =============================================================================

def test_arc_select_victim_returns_none_when_ineligible():
    """ARCCache.select_victim returns None (not a tuple) when all candidates are ineligible."""
    arc = ARCCache(capacity=3)
    p1 = VirtualPage(1, 10)
    p2 = VirtualPage(1, 20)
    p3 = VirtualPage(1, 30)

    # Warm up T1
    arc.T1[p1] = None
    arc.T1[p2] = None
    arc.T1[p3] = None

    plan = arc.prepare_request(VirtualPage(2, 40))
    assert plan.requires_eviction is True

    # When all pages are filtered out
    victim = arc.select_victim(plan=plan, is_eligible_cb=lambda p: False)
    assert victim is None
    assert not isinstance(victim, tuple)

    # Verify pending plan was safely updated with victim=None
    assert arc._pending_plan.victim is None
    assert arc._pending_plan.eviction_type is None
    assert arc._pending_plan.requires_eviction is False

    # Verify zero cache state mutation
    assert list(arc.T1.keys()) == [p1, p2, p3]
    assert arc.p == 0.0


def test_arc_quota_constrained_primary_and_alternate_list_selection():
    """ARC falls back between T1 and T2 when the primary list contains no eligible candidate."""
    # Scenario A: Primary is T1 (|T1| > p), but all T1 pages are ineligible.
    # Should fall back to T2.
    arc = ARCCache(capacity=4, use_integer_p=True)
    arc.p = 1
    t1_p1 = VirtualPage(1, 10)
    t1_p2 = VirtualPage(1, 20)
    t2_p1 = VirtualPage(2, 30)
    t2_p2 = VirtualPage(2, 40)

    arc.T1[t1_p1] = None
    arc.T1[t1_p2] = None  # |T1| = 2 > p (1) -> Primary is T1
    arc.T2[t2_p1] = None
    arc.T2[t2_p2] = None

    plan = arc.prepare_request(VirtualPage(3, 50))
    assert plan.case == "CASE_4B_L1_LESS"

    # Only PID 2 pages are eligible (in T2)
    victim = arc.select_victim(plan=plan, is_eligible_cb=lambda p: p.pid == 2)
    assert victim == t2_p1
    assert arc._pending_plan.victim == t2_p1
    assert arc._pending_plan.eviction_type == "T2->B2"

    # Scenario B: Primary is T2 (|T1| <= p), but all T2 pages are ineligible.
    # Should fall back to T1.
    arc.reset()
    arc.p = 3
    arc.T1[t1_p1] = None  # |T1| = 1 <= p (3) -> Primary is T2
    arc.T2[t2_p1] = None
    arc.T2[t2_p2] = None

    plan = arc.prepare_request(VirtualPage(3, 50))
    # Only PID 1 pages are eligible (in T1)
    victim = arc.select_victim(plan=plan, is_eligible_cb=lambda p: p.pid == 1)
    assert victim == t1_p1
    assert arc._pending_plan.victim == t1_p1
    assert arc._pending_plan.eviction_type == "T1->B1"


def test_arc_p_unchanged_during_eligibility_fallback():
    """ARC adaptation parameter p is strictly unchanged during eligibility quota fallback."""
    arc = ARCCache(capacity=4, use_integer_p=True)
    arc.p = 1
    p_initial = arc.p

    t1_p1 = VirtualPage(1, 10)
    t2_p1 = VirtualPage(2, 20)
    arc.T1[t1_p1] = None
    arc.T2[t2_p1] = None

    plan = arc.prepare_request(VirtualPage(3, 30))
    # Filter forces fallback to T2
    victim = arc.select_victim(plan=plan, is_eligible_cb=lambda p: p.pid == 2)
    assert victim == t2_p1

    # Verify p is unchanged in cache and plan target_p
    assert arc.p == p_initial
    assert plan.target_p == p_initial

    # Commit eviction and insert
    arc.evict_victim(victim, plan)
    arc.insert_page(VirtualPage(3, 30), plan=plan)
    assert arc.p == p_initial


def test_arc_victim_entering_correct_ghost_list():
    """When fallback selects from alternate list, victim enters the corresponding ghost list."""
    arc = ARCCache(capacity=4, use_integer_p=True)
    arc.p = 1  # |T1| > p -> primary is T1

    t1_p1 = VirtualPage(1, 10)
    t1_p2 = VirtualPage(1, 20)
    t2_p1 = VirtualPage(2, 30)

    arc.T1[t1_p1] = None
    arc.T1[t1_p2] = None
    arc.T2[t2_p1] = None

    incoming = VirtualPage(3, 40)
    plan = arc.prepare_request(incoming)

    # Force fallback to T2
    victim = arc.select_victim(plan=plan, is_eligible_cb=lambda p: p.pid == 2)
    assert victim == t2_p1
    assert arc._pending_plan.eviction_type == "T2->B2"

    arc.evict_victim(victim, plan)
    assert victim not in arc.T2
    assert victim in arc.B2  # Correctly entered B2, NOT B1!
    assert victim not in arc.B1

    arc.insert_page(incoming, plan=plan)
    arc.commit_request(plan)
    assert incoming in arc.T1


def test_arc_direct_t1_drop_versus_ghost_pruning():
    """Distinguish Case 4A direct T1 drop (|T1| == c) from ghost pruning (|T1| < c with |L1| == c)."""
    # 1. Ghost pruning (|T1| < c, |L1| == c):
    # |T1| = 2, |B1| = 2, c = 4
    arc = ARCCache(capacity=4, use_integer_p=True)
    t1_1, t1_2 = VirtualPage(1, 1), VirtualPage(1, 2)
    b1_1, b1_2 = VirtualPage(1, 101), VirtualPage(1, 102)

    arc.T1[t1_1] = None
    arc.T1[t1_2] = None
    arc.B1[b1_1] = None
    arc.B1[b1_2] = None

    incoming = VirtualPage(2, 50)
    plan = arc.prepare_request(incoming)
    assert plan.case == "CASE_4A_L1_FULL"
    assert plan.ghost_evicted == b1_1  # Oldest in B1 scheduled for ghost prune

    victim = arc.select_victim(plan=plan, is_eligible_cb=lambda p: p == t1_1)
    assert victim == t1_1
    assert arc._pending_plan.eviction_type == "T1->B1"

    arc.evict_victim(victim, plan)
    assert b1_1 not in arc.B1  # Ghost pruned
    assert t1_1 in arc.B1       # Resident page moved to B1

    # 2. Direct T1 drop (|T1| == c):
    # |T1| = 4, |B1| = 0, c = 4
    arc.reset()
    p_pages = [VirtualPage(1, i) for i in range(4)]
    for p in p_pages:
        arc.T1[p] = None

    incoming2 = VirtualPage(2, 60)
    plan2 = arc.prepare_request(incoming2)
    assert plan2.case == "CASE_4A_L1_FULL"
    assert plan2.ghost_evicted is None  # NO ghost pruning!
    assert plan2.eviction_type == "T1_EVICT"

    # Select eligible candidate
    victim2 = arc.select_victim(plan=plan2, is_eligible_cb=lambda p: p == p_pages[1])
    assert victim2 == p_pages[1]
    assert arc._pending_plan.eviction_type == "T1_EVICT"

    arc.evict_victim(victim2, plan2)
    assert victim2 not in arc.T1
    assert victim2 not in arc.B1  # Direct drop: NEVER entered B1!


# =============================================================================
# 3. CLOCK Eligibility Filtering and Non-Mutating Simulation
# =============================================================================

def test_clock_select_victim_returns_none_when_ineligible():
    """CLOCKCache.select_victim returns None when all candidates are ineligible."""
    clock = CLOCKCache(capacity=3)
    p1 = VirtualPage(1, 10)
    p2 = VirtualPage(1, 20)
    p3 = VirtualPage(1, 30)

    clock.slots[0] = p1
    clock.slots[1] = p2
    clock.slots[2] = p3
    clock.page_to_slot[p1] = 0
    clock.page_to_slot[p2] = 1
    clock.page_to_slot[p3] = 2

    # All pages ineligible
    token = clock.select_victim(is_eligible_cb=lambda p: False)
    assert token is None
    assert clock.hand == 0
    assert clock.state_version == 0


def test_clock_eligibility_skips_without_clearing_reference_bit():
    """CLOCK sweep skips ineligible frames without clearing their reference bits."""
    clock = CLOCKCache(capacity=3)
    p1 = VirtualPage(1, 10)  # Ineligible, ref_bit = 1
    p2 = VirtualPage(2, 20)  # Eligible, ref_bit = 1
    p3 = VirtualPage(2, 30)  # Eligible, ref_bit = 0

    clock.slots[0] = p1
    clock.slots[1] = p2
    clock.slots[2] = p3
    clock.page_to_slot[p1] = 0
    clock.page_to_slot[p2] = 1
    clock.page_to_slot[p3] = 2
    clock.reference_bits[0] = 1
    clock.reference_bits[1] = 1
    clock.reference_bits[2] = 0
    clock.hand = 0

    # Mock hardware frame reference bit reader
    def ref_reader(fid: int) -> bool:
        return bool(clock.reference_bits[fid])

    # Filter: Only PID 2 is eligible (p1 at slot 0 is shielded)
    token = clock.select_victim(get_frame_ref_bit=ref_reader, is_eligible_cb=lambda p: p.pid == 2)
    assert token is not None
    assert isinstance(token, ClockVictimToken)
    assert token.victim_page == p3
    assert token.victim_frame_id == 2

    # Slot 0 was skipped because p1 was ineligible: its reference bit was NOT cleared!
    # Slot 1 was eligible with bit 1: simulated cleared
    assert 0 not in token.cleared_frames
    assert 1 in token.cleared_frames

    # Verify simulation was non-mutating: actual bits and hand are untouched before commit
    assert clock.reference_bits[0] == 1
    assert clock.reference_bits[1] == 1
    assert clock.hand == 0


# =============================================================================
# 4. OPT Eligibility Filtering and Non-Mutating Simulation
# =============================================================================

def test_opt_select_victim_returns_none_when_ineligible():
    """OptimalCache.select_victim returns None when all candidates are ineligible."""
    seq = [VirtualPage(1, 1), VirtualPage(1, 2), VirtualPage(1, 3), VirtualPage(2, 4)]
    opt = OptimalCache(capacity=3, reference_sequence=seq)

    p1, p2, p3 = seq[0], seq[1], seq[2]
    opt.cache[p1] = 0
    opt.cache[p2] = 1
    opt.cache[p3] = 2

    # Filter rejects all
    victim = opt.select_victim(is_eligible_cb=lambda p: False)
    assert victim is None
    assert opt.current_step == 0


def test_opt_eligibility_furthest_future_eligible():
    """OPT selects candidate with furthest future reference among eligible pages."""
    p1 = VirtualPage(1, 1)  # next ref at index 4
    p2 = VirtualPage(1, 2)  # next ref at index 6 (furthest among PID 1)
    p3 = VirtualPage(2, 3)  # never referenced again (infinite distance, but shielded)

    # Sequence: [p1, p2, p3 (warmup), incoming, p1, ..., p2]
    incoming = VirtualPage(1, 10)
    seq = [p1, p2, p3, incoming, p1, incoming, p2]
    opt = OptimalCache(capacity=3, reference_sequence=seq)

    # Simulate warmup
    opt.request(p1)
    opt.request(p2)
    opt.request(p3)
    assert opt.current_step == 3

    # Incoming is at index 3
    # If all eligible: p3 would be selected (never referenced again, distance = inf)
    token_unfiltered = opt.select_victim()
    assert token_unfiltered == p3

    # Filter out PID 2 (shield p3): between p1 (dist=1) and p2 (dist=3), p2 must be selected
    token_filtered = opt.select_victim(is_eligible_cb=lambda p: p.pid == 1)
    assert token_filtered == p2


# =============================================================================
# 5. Process Purge Hooks and Active Transaction Guards
# =============================================================================

def test_purge_pid_rejects_during_active_transaction_arc_clock_opt():
    """ARC, CLOCK, and OPT reject purge_pid() with UnsupportedOperationError when transaction is active."""
    # 1. ARC
    arc = ARCCache(capacity=3)
    arc.T1[VirtualPage(1, 10)] = None
    arc.prepare_request(VirtualPage(2, 20))
    assert arc._pending_plan is not None
    with pytest.raises(UnsupportedOperationError, match="ARC transaction is active"):
        arc.purge_pid(1)
    arc.abort_request()
    # Now purge succeeds
    assert arc.purge_pid(1) == 1

    # 2. CLOCK
    clock = CLOCKCache(capacity=3)
    clock.bind_to_manager()
    clock.slots[0] = VirtualPage(1, 10)
    clock.page_to_slot[VirtualPage(1, 10)] = 0
    clock.prepare_request(VirtualPage(2, 20))
    assert clock._pending_token is not None
    # Rejection 1: Active transaction guard fires first
    with pytest.raises(UnsupportedOperationError, match="CLOCK transaction is active"):
        clock.purge_pid(1)
    clock.abort_request()
    # Rejection 2: Idle integrated CLOCK instance still rejects direct purge via integrated-mode guard
    with pytest.raises(UnsupportedOperationError, match="unsupported in integrated mode"):
        clock.purge_pid(1)

    # 3. OPT
    seq = [VirtualPage(1, 10), VirtualPage(2, 20)]
    opt = OptimalCache(capacity=3, reference_sequence=seq)
    opt.bind_to_manager()
    opt.prepare_request(VirtualPage(1, 10))
    assert opt._pending_token is not None
    with pytest.raises(UnsupportedOperationError, match="OPT transaction is active"):
        opt.purge_pid(1)
    opt.abort_request()
    assert opt.purge_pid(1) == 0


def test_purge_pid_safely_ignores_non_process_integer_keys():
    """All 7 algorithms safely ignore non-process keys (int, str) without AttributeError."""
    caches = [
        LRUCache(3),
        FIFOCache(3),
        MRUCache(3),
        LFUCache(3),
        CLOCKCache(3),
        ARCCache(3),
        OptimalCache(3, reference_sequence=[10, 20, "str_key"]),
    ]

    for cache in caches:
        if isinstance(cache, CLOCKCache):
            cache.slots[0] = 10
            cache.slots[1] = "str_key"
            cache.page_to_slot[10] = 0
            cache.page_to_slot["str_key"] = 1
        elif isinstance(cache, OptimalCache):
            cache.cache[10] = 0
            cache.cache["str_key"] = 1
            cache.page_to_frame[10] = 0
            cache.page_to_frame["str_key"] = 1
        elif isinstance(cache, ARCCache):
            cache.T1[10] = None
            cache.T2["str_key"] = None
        elif isinstance(cache, LFUCache):
            cache.page_to_freq[10] = 1
            cache.page_to_freq["str_key"] = 1
            cache.freq_to_pages[1] = {10: None, "str_key": None}
            cache.min_freq = 1
        else:
            cache.cache[10] = None
            cache.cache["str_key"] = None

        # Calling purge_pid on non-process keys returns 0 and raises no error
        purged = cache.purge_pid(1)
        assert purged == 0


def test_purge_pid_correctness_and_invariants_all_algorithms():
    """Verify purge_pid removes matching PID entries and maintains algorithm-specific invariants."""
    p1_a = VirtualPage(1, 10)
    p1_b = VirtualPage(1, 20)
    p2_a = VirtualPage(2, 30)

    # 1. LRU
    lru = LRUCache(3)
    lru.request(p1_a)
    lru.request(p2_a)
    lru.request(p1_b)
    assert lru.purge_pid(1) == 2
    assert list(lru.cache.keys()) == [p2_a]

    # 2. FIFO
    fifo = FIFOCache(3)
    fifo.request(p1_a)
    fifo.request(p2_a)
    assert fifo.purge_pid(1) == 1
    assert list(fifo.cache.keys()) == [p2_a]

    # 3. MRU
    mru = MRUCache(3)
    mru.request(p1_a)
    mru.request(p2_a)
    assert mru.purge_pid(1) == 1
    assert list(mru.cache.keys()) == [p2_a]

    # 4. LFU: verify min_freq maintenance
    lfu = LFUCache(3)
    lfu.request(p1_a)  # freq = 1 (min_freq = 1)
    lfu.request(p2_a)
    lfu.request(p2_a)  # freq = 2
    assert lfu.min_freq == 1
    assert lfu.purge_pid(1) == 1
    # After purging p1_a, lowest remaining freq is 2
    assert lfu.min_freq == 2
    assert list(lfu.page_to_freq.keys()) == [p2_a]

    # 5. CLOCK: verify slots and page_to_slot cleared
    clock = CLOCKCache(3)
    clock.slots[0] = p1_a
    clock.slots[1] = p2_a
    clock.page_to_slot[p1_a] = 0
    clock.page_to_slot[p2_a] = 1
    clock.reference_bits[0] = 1
    clock.reference_bits[1] = 1
    assert clock.purge_pid(1) == 1
    assert clock.slots[0] is None
    assert clock.slots[1] == p2_a
    assert p1_a not in clock.page_to_slot
    assert clock.page_to_slot[p2_a] == 1
    assert clock.reference_bits[0] == 0
    assert clock.reference_bits[1] == 1

    # 6. ARC: verify T1, T2, B1, B2 purged and p invariant 0 <= p <= c preserved
    arc = ARCCache(capacity=4, use_integer_p=True)
    arc.p = 3
    arc.T1[p1_a] = None
    arc.B1[p1_b] = None
    arc.T2[p2_a] = None
    assert arc.purge_pid(1) == 2
    assert p1_a not in arc.T1
    assert p1_b not in arc.B1
    assert p2_a in arc.T2
    # p must NOT be clamped to resident count (len(T1)+len(T2) = 1), but must be in [0, c]
    assert arc.p == 3

    # 7. OPT: verify cache purged, but future sequence remains immutable
    seq = [p1_a, p2_a, p1_a]
    opt = OptimalCache(capacity=3, reference_sequence=seq)
    opt.request(p1_a)
    opt.request(p2_a)
    assert p1_a in opt.cache
    assert opt.purge_pid(1) == 1
    assert p1_a not in opt.cache
    assert p2_a in opt.cache
    # Future sequence and indices are immutable
    assert opt.sequence == [p1_a, p2_a, p1_a]


def test_clock_integrated_purge_pid_guarded_and_leaves_state_unchanged():
    """Verify integrated CLOCKCache.purge_pid() raises UnsupportedOperationError and leaves state untouched."""
    mm = MemoryManager(capacity=3, algorithm="CLOCK")
    clock: CLOCKCache = mm.policy.policy
    assert clock._is_integrated is True

    # Populate pages via MemoryManager
    mm.access_page(pid=1, vpn=10)
    mm.access_page(pid=2, vpn=20)

    # Capture state before attempted purge
    slots_before = list(clock.slots)
    page_to_slot_before = dict(clock.page_to_slot)
    version_before = clock.state_version
    frame0_ref_before = mm.frames[0].reference_bit
    frame0_alloc_before = mm.frames[0].is_allocated
    frame0_vpage_before = mm.frames[0].vpage

    # Attempt direct purge of PID 1 on integrated CLOCK instance
    with pytest.raises(UnsupportedOperationError, match="unsupported in integrated mode"):
        clock.purge_pid(1)

    # Verify zero state mutation in CLOCK cache
    assert clock.slots == slots_before
    assert clock.page_to_slot == page_to_slot_before
    assert clock.state_version == version_before

    # Verify zero state mutation in MemoryManager physical frames
    assert mm.frames[0].reference_bit == frame0_ref_before
    assert mm.frames[0].is_allocated == frame0_alloc_before
    assert mm.frames[0].vpage == frame0_vpage_before

