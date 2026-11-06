"""
Unit and integration tests for Stage 12.2 Phase 2:
Policy boundary safety, supported/unsupported algorithm enforcement,
and LFU / MRU lifecycle verification through MemoryManager.
"""
from backend.algorithms.arc import ARCCache
from backend.algorithms.clock import CLOCKCache
from backend.algorithms.fifo import FIFOCache
from backend.algorithms.lfu import LFUCache
from backend.algorithms.lru import LRUCache
from backend.algorithms.mru import MRUCache
from backend.algorithms.opt import OptimalCache
from backend.memory.exceptions import UnsupportedPolicyError
from backend.memory.manager import MemoryManager
from backend.memory.page import VirtualPage
import pytest


# =============================================================================
# 1. Policy Boundary Tests
# =============================================================================

def test_supported_policy_initialization():
    """Verify that LRU, FIFO, LFU, MRU, ARC, CLOCK, and OPT initialize cleanly by string and instance."""
    # By string
    mm_lru = MemoryManager(capacity=3, algorithm="LRU")
    assert mm_lru.policy.name == "LRUCache"

    mm_fifo = MemoryManager(capacity=3, algorithm="FIFO")
    assert mm_fifo.policy.name == "FIFOCache"

    mm_lfu = MemoryManager(capacity=3, algorithm="LFU")
    assert mm_lfu.policy.name == "LFUCache"

    mm_mru = MemoryManager(capacity=3, algorithm="MRU")
    assert mm_mru.policy.name == "MRUCache"

    mm_arc = MemoryManager(capacity=3, algorithm="ARC")
    assert mm_arc.policy.name == "ARCCache"

    mm_clock = MemoryManager(capacity=3, algorithm="CLOCK")
    assert mm_clock.policy.name == "CLOCKCache"

    mm_opt = MemoryManager(capacity=3, algorithm="OPT", reference_sequence=[1, 2, 3])
    assert mm_opt.policy.name == "OptimalCache"

    mm_optimal = MemoryManager(capacity=3, algorithm="OPTIMAL", reference_sequence=[1, 2, 3])
    assert mm_optimal.policy.name == "OptimalCache"

    # By instance
    assert MemoryManager(capacity=3, algorithm=LRUCache(3)).policy.name == "LRUCache"
    assert MemoryManager(capacity=3, algorithm=FIFOCache(3)).policy.name == "FIFOCache"
    assert MemoryManager(capacity=3, algorithm=LFUCache(3)).policy.name == "LFUCache"
    assert MemoryManager(capacity=3, algorithm=MRUCache(3)).policy.name == "MRUCache"
    assert MemoryManager(capacity=3, algorithm=ARCCache(3)).policy.name == "ARCCache"
    assert MemoryManager(capacity=3, algorithm=CLOCKCache(3)).policy.name == "CLOCKCache"
    assert MemoryManager(capacity=3, algorithm=OptimalCache(3, reference_sequence=[1, 2, 3])).policy.name == "OptimalCache"


def test_unsupported_policy_strings_rejected():
    """Verify unknown policy strings raise UnsupportedPolicyError."""
    with pytest.raises(UnsupportedPolicyError) as exc_unknown:
        MemoryManager(capacity=3, algorithm="UNKNOWN_ALGO")
    assert "Unknown replacement algorithm" in str(exc_unknown.value)


def test_unsupported_policy_instances_rejected():
    """Verify unknown types raise UnsupportedPolicyError."""
    with pytest.raises(UnsupportedPolicyError):
        MemoryManager(capacity=3, algorithm=object())


def test_rejected_initialization_does_not_mutate_instances():
    """Verify rejecting non-pristine algorithm configurations causes zero state mutation."""
    from backend.memory.exceptions import BindingError
    opt = OptimalCache(3, reference_sequence=[10, 20, 30])
    opt.request(10)  # Make non-pristine
    opt_step_before = opt.current_step
    opt_cache_before = dict(opt.cache)
    opt_queues_before = {p: list(q) for p, q in opt.page_to_future_indices.items()}

    with pytest.raises(BindingError):
        MemoryManager(capacity=3, algorithm=opt)

    assert opt.current_step == opt_step_before
    assert dict(opt.cache) == opt_cache_before
    assert {p: list(q) for p, q in opt.page_to_future_indices.items()} == opt_queues_before
    assert opt.requests == 1


# =============================================================================
# 2. LFU Integration Verification
# =============================================================================

def test_lfu_memory_manager_lifecycle():
    """Verify LFU frequency increments on hits, min-frequency eviction, and tie-breaking."""
    mm = MemoryManager(capacity=3, algorithm="LFU")

    # 1. Cold misses: pages (1, 10), (1, 20), (1, 30) loaded with initial freq 1
    r1 = mm.access_page(1, 10)
    r2 = mm.access_page(1, 20)
    r3 = mm.access_page(1, 30)
    assert not r1.hit and not r2.hit and not r3.hit

    # 2. Hits on page 10 (2 hits -> freq 3) and page 20 (1 hit -> freq 2)
    mm.access_page(1, 10)
    mm.access_page(1, 10)
    mm.access_page(1, 20)

    # Currently:
    # Page 10: freq 3
    # Page 20: freq 2
    # Page 30: freq 1 (min_freq = 1)

    # 3. Access page 40 -> triggers eviction of page 30 (min_freq = 1)
    r4 = mm.access_page(1, 40)
    assert r4.hit is False
    assert r4.evicted_page == VirtualPage(1, 30)
    assert mm.metrics.successful_evictions == 1

    # 4. Now resident: 10 (freq 3), 20 (freq 2), 40 (freq 1)
    # Promote page 40 to freq 2 (1 hit)
    mm.access_page(1, 40)

    # Currently:
    # Page 10: freq 3
    # Page 20: freq 2 (arrived first)
    # Page 40: freq 2 (arrived second)
    # Min frequency is 2. Tie between page 20 and 40.
    # Deterministic LRU tie-breaking should evict page 20.
    r5 = mm.access_page(1, 50)
    assert r5.hit is False
    assert r5.evicted_page == VirtualPage(1, 20)

    # Invariants
    assert len(mm.get_resident_pages()) == 3
    assert set(mm.get_resident_pages()) == {
        VirtualPage(1, 10),
        VirtualPage(1, 40),
        VirtualPage(1, 50),
    }


# =============================================================================
# 3. MRU Integration Verification
# =============================================================================

def test_mru_memory_manager_lifecycle():
    """Verify MRU recency tracking on hits, tail eviction, and cache consistency."""
    mm = MemoryManager(capacity=3, algorithm="MRU")

    # 1. Cold misses: pages 1, 2, 3 loaded
    mm.access_page(1, 1)  # Frame 0
    mm.access_page(1, 2)  # Frame 1
    mm.access_page(1, 3)  # Frame 2

    # In MRU order: [1, 2, 3] where 3 is MRU (tail)

    # 2. Access page 4 (memory full): MRU victim is page 3
    r4 = mm.access_page(1, 4)
    assert r4.hit is False
    assert r4.evicted_page == VirtualPage(1, 3)

    # Resident now: [1, 2, 4] where 4 is MRU

    # 3. Hit on page 1: promoted to MRU tail -> order: [2, 4, 1]
    r_hit = mm.access_page(1, 1)
    assert r_hit.hit is True

    # 4. Access page 5 (memory full): MRU victim is now page 1
    r5 = mm.access_page(1, 5)
    assert r5.hit is False
    assert r5.evicted_page == VirtualPage(1, 1)

    # Invariants
    assert len(mm.get_resident_pages()) == 3
    assert set(mm.get_resident_pages()) == {
        VirtualPage(1, 2),
        VirtualPage(1, 4),
        VirtualPage(1, 5),
    }

