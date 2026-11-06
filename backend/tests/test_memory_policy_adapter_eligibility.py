"""
Focused unit and integration test suite for Stage 12.2 Phase 4C Substage 4C.2:
PolicyAdapter Eligibility Forwarding, Standardized VictimSelectionResult, and Strict Process-Purge Coordination.

Covers:
  1. Standardized VictimSelectionResult structure across all seven algorithms.
  2. select_victim() returning None when all candidate pages are ineligible (no ineligible victim returned).
  3. Eligibility callback forwarding across all seven algorithms.
  4. Preservation of algorithm-specific replacement order and tie-breaking.
  5. CLOCK token lifecycle and stale-token rejection through PolicyAdapter.
  6. ARC normal versus quota-constrained fallback through PolicyAdapter.
  7. OPT selection immutability (does not advance sequence or future queues).
  8. Strict process-purge delegation to underlying algorithms.
  9. Rejection of integrated CLOCK purge through PolicyAdapter.
  10. Rejection of purge when policy is missing purge_pid().
  11. Rejection of purge during active pending token in PolicyAdapter.
"""
from unittest.mock import MagicMock
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
from backend.memory.policy_adapter import PolicyAdapter, VictimSelectionResult


# =============================================================================
# 1. Standardized VictimSelectionResult Structure Across All Seven Algorithms
# =============================================================================

def test_adapter_select_victim_standardized_result_structure():
    """Verify PolicyAdapter.select_victim returns typed VictimSelectionResult for all 7 algorithms."""
    p1 = VirtualPage(1, 10)
    p2 = VirtualPage(1, 20)

    # 1. LRU
    adapter_lru = PolicyAdapter("LRU", capacity=2)
    adapter_lru.insert_page(p1, 0)
    adapter_lru.insert_page(p2, 1)
    res_lru = adapter_lru.select_victim()
    assert isinstance(res_lru, VictimSelectionResult)
    assert res_lru.victim == p1
    assert res_lru.frame_id is None
    assert res_lru.token is None
    assert res_lru.eviction_path == "LRUCache"

    # 2. FIFO
    adapter_fifo = PolicyAdapter("FIFO", capacity=2)
    adapter_fifo.insert_page(p1, 0)
    adapter_fifo.insert_page(p2, 1)
    res_fifo = adapter_fifo.select_victim()
    assert isinstance(res_fifo, VictimSelectionResult)
    assert res_fifo.victim == p1
    assert res_fifo.frame_id is None
    assert res_fifo.token is None
    assert res_fifo.eviction_path == "FIFOCache"

    # 3. MRU
    adapter_mru = PolicyAdapter("MRU", capacity=2)
    adapter_mru.insert_page(p1, 0)
    adapter_mru.insert_page(p2, 1)
    res_mru = adapter_mru.select_victim()
    assert isinstance(res_mru, VictimSelectionResult)
    assert res_mru.victim == p2  # Most recently used
    assert res_mru.frame_id is None
    assert res_mru.token is None
    assert res_mru.eviction_path == "MRUCache"

    # 4. LFU
    adapter_lfu = PolicyAdapter("LFU", capacity=2)
    adapter_lfu.insert_page(p1, 0)
    adapter_lfu.insert_page(p2, 1)
    res_lfu = adapter_lfu.select_victim()
    assert isinstance(res_lfu, VictimSelectionResult)
    assert res_lfu.victim == p1
    assert res_lfu.frame_id is None
    assert res_lfu.token is None
    assert res_lfu.eviction_path == "LFUCache"

    # 5. ARC
    adapter_arc = PolicyAdapter("ARC", capacity=2)
    plan_arc = adapter_arc.validate_request(p1)
    adapter_arc.insert_page(p1, 0, plan_arc)
    adapter_arc.commit_request(plan_arc)
    plan_arc2 = adapter_arc.validate_request(p2)
    adapter_arc.insert_page(p2, 1, plan_arc2)
    adapter_arc.commit_request(plan_arc2)

    incoming = VirtualPage(1, 30)
    val_arc = adapter_arc.validate_request(incoming)
    res_arc = adapter_arc.select_victim(token=val_arc)
    assert isinstance(res_arc, VictimSelectionResult)
    assert res_arc.victim == p1
    assert res_arc.frame_id is None
    assert isinstance(res_arc.token, ArcPlan)
    assert res_arc.eviction_path in ("T1->B1", "T1_EVICT")

    # 6. CLOCK
    clock_cache = CLOCKCache(capacity=2)
    adapter_clock = PolicyAdapter(clock_cache, capacity=2, get_frame_ref_bit=lambda fid: False)
    val_clk1 = adapter_clock.validate_request(p1)
    adapter_clock.insert_page(p1, 0, val_clk1)
    adapter_clock.commit_request(val_clk1)
    val_clk2 = adapter_clock.validate_request(p2)
    adapter_clock.insert_page(p2, 1, val_clk2)
    adapter_clock.commit_request(val_clk2)

    val_clk3 = adapter_clock.validate_request(incoming)
    res_clock = adapter_clock.select_victim(token=val_clk3)
    assert isinstance(res_clock, VictimSelectionResult)
    assert res_clock.victim == p1
    assert res_clock.frame_id == 0
    assert isinstance(res_clock.token, ClockVictimToken)
    assert res_clock.eviction_path == "CLOCK_SWEEP"

    # 7. OPT
    seq = [p1, p2, incoming]
    opt_cache = OptimalCache(capacity=2, reference_sequence=seq)
    adapter_opt = PolicyAdapter(opt_cache, capacity=2, reference_sequence=seq)
    val_opt1 = adapter_opt.validate_request(p1)
    adapter_opt.insert_page(p1, 0, val_opt1)
    adapter_opt.commit_request(val_opt1)
    val_opt2 = adapter_opt.validate_request(p2)
    adapter_opt.insert_page(p2, 1, val_opt2)
    adapter_opt.commit_request(val_opt2)

    val_opt3 = adapter_opt.validate_request(incoming)
    res_opt = adapter_opt.select_victim(token=val_opt3)
    assert isinstance(res_opt, VictimSelectionResult)
    assert res_opt.victim in (p1, p2)
    assert res_opt.frame_id in (0, 1)
    assert isinstance(res_opt.token, OptVictimToken)
    assert res_opt.eviction_path == "OPT_FURTHEST_FUTURE"


# =============================================================================
# 2. None Return When Ineligible and Eligibility Forwarding
# =============================================================================

def test_adapter_select_victim_returns_none_when_all_ineligible():
    """Verify PolicyAdapter.select_victim returns None when all candidates fail is_eligible_cb."""
    p1 = VirtualPage(1, 10)
    p2 = VirtualPage(1, 20)
    p3 = VirtualPage(2, 30)

    policies = [
        PolicyAdapter("LRU", 2),
        PolicyAdapter("FIFO", 2),
        PolicyAdapter("MRU", 2),
        PolicyAdapter("LFU", 2),
    ]

    for adapter in policies:
        adapter.insert_page(p1, 0)
        adapter.insert_page(p2, 1)
        # All pages filtered out
        selection = adapter.select_victim(is_eligible_cb=lambda p: False)
        assert selection is None

    # ARC
    arc_adapter = PolicyAdapter("ARC", 2)
    t1 = arc_adapter.validate_request(p1)
    arc_adapter.insert_page(p1, 0, t1)
    arc_adapter.commit_request(t1)
    t2 = arc_adapter.validate_request(p2)
    arc_adapter.insert_page(p2, 1, t2)
    arc_adapter.commit_request(t2)
    val_arc = arc_adapter.validate_request(p3)
    assert arc_adapter.select_victim(token=val_arc, is_eligible_cb=lambda p: False) is None

    # CLOCK
    clock = CLOCKCache(2)
    clock_adapter = PolicyAdapter(clock, 2, get_frame_ref_bit=lambda f: False)
    c1 = clock_adapter.validate_request(p1)
    clock_adapter.insert_page(p1, 0, c1)
    clock_adapter.commit_request(c1)
    c2 = clock_adapter.validate_request(p2)
    clock_adapter.insert_page(p2, 1, c2)
    clock_adapter.commit_request(c2)
    val_clk = clock_adapter.validate_request(p3)
    assert clock_adapter.select_victim(token=val_clk, is_eligible_cb=lambda p: False) is None

    # OPT
    seq = [p1, p2, p3]
    opt = OptimalCache(2, reference_sequence=seq)
    opt_adapter = PolicyAdapter(opt, 2, reference_sequence=seq)
    o1 = opt_adapter.validate_request(p1)
    opt_adapter.insert_page(p1, 0, o1)
    opt_adapter.commit_request(o1)
    o2 = opt_adapter.validate_request(p2)
    opt_adapter.insert_page(p2, 1, o2)
    opt_adapter.commit_request(o2)
    val_opt = opt_adapter.validate_request(p3)
    assert opt_adapter.select_victim(token=val_opt, is_eligible_cb=lambda p: False) is None


def test_adapter_forwards_eligibility_callback_all_policies():
    """Verify is_eligible_cb correctly filters out ineligible candidates across all 7 policies."""
    p1 = VirtualPage(1, 10)  # PID 1 (Ineligible)
    p2 = VirtualPage(2, 20)  # PID 2 (Eligible)

    # 1. LRU: without filter would pick p1; with filter must pick p2
    lru = PolicyAdapter("LRU", 2)
    lru.insert_page(p1, 0)
    lru.insert_page(p2, 1)
    res = lru.select_victim(is_eligible_cb=lambda p: p.pid == 2)
    assert res is not None
    assert res.victim == p2

    # 2. FIFO: without filter would pick p1; with filter must pick p2
    fifo = PolicyAdapter("FIFO", 2)
    fifo.insert_page(p1, 0)
    fifo.insert_page(p2, 1)
    res = fifo.select_victim(is_eligible_cb=lambda p: p.pid == 2)
    assert res is not None
    assert res.victim == p2

    # 3. MRU: without filter would pick p2; with filter for PID 1 must pick p1
    mru = PolicyAdapter("MRU", 2)
    mru.insert_page(p1, 0)
    mru.insert_page(p2, 1)
    res = mru.select_victim(is_eligible_cb=lambda p: p.pid == 1)
    assert res is not None
    assert res.victim == p1

    # 4. LFU: without filter picks p1 (lowest freq); with filter for PID 2 must pick p2
    lfu = PolicyAdapter("LFU", 2)
    lfu.insert_page(p1, 0)
    lfu.insert_page(p2, 1)
    res = lfu.select_victim(is_eligible_cb=lambda p: p.pid == 2)
    assert res is not None
    assert res.victim == p2

    # 5. ARC: filter selects eligible candidate
    arc = PolicyAdapter("ARC", 2)
    t1 = arc.validate_request(p1)
    arc.insert_page(p1, 0, t1)
    arc.commit_request(t1)
    t2 = arc.validate_request(p2)
    arc.insert_page(p2, 1, t2)
    arc.commit_request(t2)
    incoming = VirtualPage(3, 30)
    val = arc.validate_request(incoming)
    res = arc.select_victim(token=val, is_eligible_cb=lambda p: p.pid == 2)
    assert res is not None
    assert res.victim == p2

    # 6. CLOCK: filter skips p1 at slot 0 and picks p2 at slot 1
    clk = PolicyAdapter(CLOCKCache(2), 2, get_frame_ref_bit=lambda f: False)
    t1 = clk.validate_request(p1)
    clk.insert_page(p1, 0, t1)
    clk.commit_request(t1)
    t2 = clk.validate_request(p2)
    clk.insert_page(p2, 1, t2)
    clk.commit_request(t2)
    val = clk.validate_request(incoming)
    res = clk.select_victim(token=val, is_eligible_cb=lambda p: p.pid == 2)
    assert res is not None
    assert res.victim == p2
    assert res.frame_id == 1

    # 7. OPT: filter selects eligible candidate
    seq = [p1, p2, incoming, p2]
    opt = PolicyAdapter(OptimalCache(2, reference_sequence=seq), 2, reference_sequence=seq)
    t1 = opt.validate_request(p1)
    opt.insert_page(p1, 0, t1)
    opt.commit_request(t1)
    t2 = opt.validate_request(p2)
    opt.insert_page(p2, 1, t2)
    opt.commit_request(t2)
    val = opt.validate_request(incoming)
    res = opt.select_victim(token=val, is_eligible_cb=lambda p: p.pid == 2)
    assert res is not None
    assert res.victim == p2


# =============================================================================
# 3. ARC Quota Fallback and CLOCK/OPT Token Lifecycles
# =============================================================================

def test_adapter_arc_normal_versus_quota_constrained_fallback():
    """Verify ARC through PolicyAdapter selects alternate list when primary list is ineligible."""
    arc_cache = ARCCache(capacity=4, use_integer_p=True)
    arc_cache.p = 1  # |T1| > 1 -> primary is T1
    adapter = PolicyAdapter(arc_cache, capacity=4)

    t1_p1 = VirtualPage(1, 10)
    t1_p2 = VirtualPage(1, 20)
    t2_p1 = VirtualPage(2, 30)

    arc_cache.T1[t1_p1] = None
    arc_cache.T1[t1_p2] = None
    arc_cache.T2[t2_p1] = None

    incoming = VirtualPage(3, 40)
    val_plan = adapter.validate_request(incoming)

    # Primary list is T1 (PID 1). Filter out PID 1 -> fallback to T2 (PID 2)
    result = adapter.select_victim(token=val_plan, is_eligible_cb=lambda p: p.pid == 2)
    assert result is not None
    assert result.victim == t2_p1
    assert result.eviction_path == "T2->B2"
    # p adaptation parameter must be strictly preserved
    assert arc_cache.p == 1

    # Commit eviction via adapter
    adapter.evict_victim(result.victim, result.token)
    assert t2_p1 not in arc_cache.T2
    assert t2_p1 in arc_cache.B2  # Reached correct ghost list


def test_adapter_opt_selection_does_not_advance_sequence():
    """Verify calling PolicyAdapter.select_victim() on OPT does not advance sequence step."""
    p1 = VirtualPage(1, 10)
    p2 = VirtualPage(1, 20)
    p3 = VirtualPage(1, 30)
    seq = [p1, p2, p3]

    opt_cache = OptimalCache(capacity=2, reference_sequence=seq)
    adapter = PolicyAdapter(opt_cache, capacity=2, reference_sequence=seq)

    t1 = adapter.validate_request(p1)
    adapter.insert_page(p1, 0, t1)
    adapter.commit_request(t1)

    t2 = adapter.validate_request(p2)
    adapter.insert_page(p2, 1, t2)
    adapter.commit_request(t2)

    step_before = opt_cache.current_step
    version_before = opt_cache.state_version

    # Repeatedly call select_victim
    val_token = adapter.validate_request(p3)
    for _ in range(3):
        res = adapter.select_victim(token=val_token)
        assert res is not None
        assert opt_cache.current_step == step_before
        assert opt_cache.state_version == version_before

    # Future indices and sequence intact
    assert opt_cache.sequence == seq


def test_adapter_clock_token_lifecycle_and_stale_token_protection():
    """Verify CLOCK through PolicyAdapter creates valid token and rejects stale commit."""
    clock_cache = CLOCKCache(2)
    ref_bits = {0: True, 1: False}
    adapter = PolicyAdapter(clock_cache, 2, get_frame_ref_bit=lambda f: ref_bits.get(f, False))

    p1, p2, p3 = VirtualPage(1, 1), VirtualPage(1, 2), VirtualPage(1, 3)
    t1 = adapter.validate_request(p1)
    adapter.insert_page(p1, 0, t1)
    adapter.commit_request(t1)

    t2 = adapter.validate_request(p2)
    adapter.insert_page(p2, 1, t2)
    adapter.commit_request(t2)

    val_token = adapter.validate_request(p3)
    victim_res = adapter.select_victim(token=val_token)
    assert isinstance(victim_res, VictimSelectionResult)
    assert victim_res.frame_id == 1  # bit 0 was True, bit 1 was False

    # Simulate intervening external mutation advancing clock state_version
    clock_cache.notify_frame_bit_changed(0)

    # Stale token must be rejected on evict
    with pytest.raises(StaleTokenError):
        adapter.evict_victim(victim_res.victim, victim_res.token)


# =============================================================================
# 4. Process Purge Delegation and Active Transaction Guards
# =============================================================================

def test_adapter_purge_process_delegates_strictly():
    """Verify purge_process(pid) delegates strictly to underlying algorithms."""
    p1_a = VirtualPage(1, 10)
    p1_b = VirtualPage(1, 20)
    p2_a = VirtualPage(2, 30)

    # 1. LRU
    lru_adapter = PolicyAdapter("LRU", 3)
    lru_adapter.insert_page(p1_a, 0)
    lru_adapter.insert_page(p2_a, 1)
    lru_adapter.insert_page(p1_b, 2)
    assert lru_adapter.purge_process(1) == 2
    assert lru_adapter.contains(p2_a) is True
    assert lru_adapter.contains(p1_a) is False

    # 2. FIFO
    fifo_adapter = PolicyAdapter("FIFO", 3)
    fifo_adapter.insert_page(p1_a, 0)
    fifo_adapter.insert_page(p2_a, 1)
    assert fifo_adapter.purge_process(1) == 1
    assert fifo_adapter.contains(p2_a) is True
    assert fifo_adapter.contains(p1_a) is False

    # 3. LFU
    lfu_adapter = PolicyAdapter("LFU", 3)
    lfu_adapter.insert_page(p1_a, 0)
    lfu_adapter.insert_page(p2_a, 1)
    assert lfu_adapter.purge_process(1) == 1
    assert lfu_adapter.contains(p2_a) is True

    # 4. MRU
    mru_adapter = PolicyAdapter("MRU", 3)
    mru_adapter.insert_page(p1_a, 0)
    mru_adapter.insert_page(p2_a, 1)
    assert mru_adapter.purge_process(1) == 1
    assert mru_adapter.contains(p2_a) is True

    # 5. ARC
    arc_adapter = PolicyAdapter("ARC", 3)
    arc_cache = arc_adapter.policy
    arc_cache.T1[p1_a] = None
    arc_cache.B1[p1_b] = None
    arc_cache.T2[p2_a] = None
    assert arc_adapter.purge_process(1) == 2
    assert p1_a not in arc_cache.T1
    assert p2_a in arc_cache.T2

    # 6. OPT
    seq = [p1_a, p2_a]
    opt_adapter = PolicyAdapter(OptimalCache(2, reference_sequence=seq), 2, reference_sequence=seq)
    opt_cache = opt_adapter.policy
    opt_cache.cache[p1_a] = 0
    opt_cache.cache[p2_a] = 1
    assert opt_adapter.purge_process(1) == 1
    assert p1_a not in opt_cache.cache
    assert p2_a in opt_cache.cache


def test_adapter_integrated_clock_purge_rejected():
    """Verify calling purge_process() through PolicyAdapter on integrated CLOCK raises UnsupportedOperationError."""
    # PolicyAdapter binds pristine CLOCKCache to manager mode during init
    clock = CLOCKCache(3)
    adapter = PolicyAdapter(clock, 3)
    assert clock._is_integrated is True

    clock.slots[0] = VirtualPage(1, 10)
    clock.page_to_slot[VirtualPage(1, 10)] = 0
    slots_before = list(clock.slots)
    page_to_slot_before = dict(clock.page_to_slot)

    with pytest.raises(UnsupportedOperationError, match="unsupported in integrated mode"):
        adapter.purge_process(1)

    # State must be completely unchanged
    assert clock.slots == slots_before
    assert clock.page_to_slot == page_to_slot_before


def test_adapter_rejects_missing_purge_pid(monkeypatch):
    """Verify PolicyAdapter.purge_process() raises UnsupportedOperationError if policy lacks purge_pid."""
    adapter = PolicyAdapter("LRU", capacity=3)
    # Remove purge_pid dynamically from the policy class
    monkeypatch.delattr(LRUCache, "purge_pid", raising=False)
    with pytest.raises(UnsupportedOperationError, match="missing purge_pid"):
        adapter.purge_process(1)


def test_adapter_purge_rejected_during_active_transaction():
    """Verify purge_process() is rejected with UnsupportedOperationError when adapter has pending token."""
    adapter = PolicyAdapter("LRU", 3)
    p1 = VirtualPage(1, 10)
    adapter.insert_page(p1, 0)

    # Set pending token
    adapter._pending_token = "ACTIVE_TRANSACTION_TOKEN"

    with pytest.raises(UnsupportedOperationError, match="active pending token"):
        adapter.purge_process(1)

    # After clearing pending token via abort, purge succeeds
    adapter.abort_request()
    assert adapter._pending_token is None
    assert adapter.purge_process(1) == 1


# =============================================================================
# 5. Targeted Frame ID Extraction and Frame 0 Preservation Tests
# =============================================================================

def test_frame_id_zero_preserved_and_not_treated_as_falsy():
    """Verify that frame_id=0 is preserved and does not fall back to pte.frame_id."""
    vpage = VirtualPage(1, 10)
    dummy_pte_frame_id = 99

    # Case 1: frame_id == 0 (must NOT be treated as falsy or fall back to dummy_pte_frame_id)
    res_zero = VictimSelectionResult(victim=vpage, frame_id=0)
    target_fid_0 = (
        res_zero.frame_id
        if res_zero.frame_id is not None
        else dummy_pte_frame_id
    )
    assert target_fid_0 == 0
    assert target_fid_0 is not dummy_pte_frame_id

    # Case 2: frame_id is positive integer
    res_pos = VictimSelectionResult(victim=vpage, frame_id=2)
    target_fid_pos = (
        res_pos.frame_id
        if res_pos.frame_id is not None
        else dummy_pte_frame_id
    )
    assert target_fid_pos == 2

    # Case 3: frame_id is None (must correctly fall back to dummy_pte_frame_id)
    res_none = VictimSelectionResult(victim=vpage, frame_id=None)
    target_fid_none = (
        res_none.frame_id
        if res_none.frame_id is not None
        else dummy_pte_frame_id
    )
    assert target_fid_none == 99


def test_clock_eviction_reuses_frame_zero_correctly():
    """Verify real MemoryManager eviction under CLOCK correctly selects and reuses frame 0."""
    mm = MemoryManager(capacity=2, algorithm="CLOCK")
    p1 = VirtualPage(1, 10)
    p2 = VirtualPage(1, 20)
    p3 = VirtualPage(1, 30)

    # Warmup memory
    r1 = mm.access_page(p1.pid, p1.vpn)
    r2 = mm.access_page(p2.pid, p2.vpn)
    assert r1.frame_id == 0
    assert r2.frame_id == 1
    assert mm.allocator.has_free_frame() is False

    # Frame 0 holds p1; clear its reference bit so it is selected as victim
    mm.set_frame_reference_bit(0, False)
    # Frame 1 holds p2; keep its reference bit True (second chance)
    mm.set_frame_reference_bit(1, True)

    # Access p3: CLOCK sweeps, selects frame 0 (victim p1)
    r3 = mm.access_page(p3.pid, p3.vpn)
    assert r3.hit is False
    assert r3.evicted_page == p1
    assert r3.frame_id == 0  # Reused frame 0

    # Verify physical frame 0 and page table state
    frame0 = mm.get_frame(0)
    assert frame0.vpage == p3
    assert frame0.is_allocated is True
    assert mm.get_page_table(1).get_frame_id(p1.vpn) is None
    assert mm.get_page_table(1).get_frame_id(p3.vpn) == 0

    # Verify CLOCK policy state
    clock = mm.policy.policy
    assert clock.slots[0] == p3
    assert clock.page_to_slot[p3] == 0
    assert p1 not in clock.page_to_slot


def test_opt_eviction_reuses_frame_zero_correctly():
    """Verify real MemoryManager eviction under OPT correctly selects and reuses frame 0."""
    p1 = VirtualPage(1, 10)
    p2 = VirtualPage(1, 20)
    p3 = VirtualPage(1, 30)
    # P1 referenced only at step 0; P2 referenced at step 1 and step 3
    seq = [p1, p2, p3, p2]

    mm = MemoryManager(capacity=2, algorithm="OPT", reference_sequence=seq)

    # Step 0: P1 into frame 0
    r1 = mm.access_page(p1.pid, p1.vpn)
    assert r1.frame_id == 0

    # Step 1: P2 into frame 1
    r2 = mm.access_page(p2.pid, p2.vpn)
    assert r2.frame_id == 1

    # Step 2: P3 causes eviction. P1 is never used again (dist=inf), P2 is used at step 3.
    # P1 (in frame 0) is chosen as victim!
    r3 = mm.access_page(p3.pid, p3.vpn)
    assert r3.hit is False
    assert r3.evicted_page == p1
    assert r3.frame_id == 0  # Reused frame 0

    # Verify physical frame 0 and page table state
    frame0 = mm.get_frame(0)
    assert frame0.vpage == p3
    assert frame0.is_allocated is True
    assert mm.get_page_table(1).get_frame_id(p1.vpn) is None
    assert mm.get_page_table(1).get_frame_id(p3.vpn) == 0

