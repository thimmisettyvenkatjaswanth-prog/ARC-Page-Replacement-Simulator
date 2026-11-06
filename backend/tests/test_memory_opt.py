"""
Comprehensive integration tests for Stage 12.2 Phase 3C:
OPT (Belady's Optimal / MIN) page replacement policy integration with MemoryManager.

Covers all 22 required verification scenarios:
  1. Successful pristine OPT binding.
  2. Rejection of non-pristine OPT binding (BindingError).
  3. Rejection of missing/empty reference sequence (ValueError).
  4. Correct sequence initialization and process identity preservation.
  5. Correct expected-page validation.
  6. Unexpected-page rejection without state mutation.
  7. Sequence exhaustion without state mutation.
  8. Optimal victim selection (furthest future reference).
  9. Never-used-again page selection (infinite future distance).
  10. Deterministic FIFO tie-breaking for equal future distances / infinity.
  11. Non-mutating victim planning in Phase 1 (zero state mutation).
  12. Stale token rejection after sequence mutation (StaleTokenError).
  13. Stale token rejection after resident mapping mutation (StaleTokenError).
  14. Free-frame allocation before eviction.
  15. Successful dirty-victim write-back.
  16. Failed write-back preserving victim state and OPT step.
  17. Failed page-in preserving OPT sequence position.
  18. Successful hit advancing the sequence exactly once.
  19. Successful miss advancing the sequence exactly once.
  20. Coordinated reset preserving integrated mode and advancing state version.
  21. Standalone OPT backward compatibility.
  22. Unexpected commit failure and panic-state behavior.
"""
from dataclasses import FrozenInstanceError
from unittest.mock import patch
import pytest

from backend.algorithms.opt import (
    OPTCache,
    OptValidationToken,
    OptVictimToken,
    OptimalCache,
)
from backend.memory.backing_store import BackingStore
from backend.memory.exceptions import (
    BindingError,
    CorruptedManagerStateError,
    DiskReadError,
    DiskWriteError,
    StaleTokenError,
    UnsupportedOperationError,
)
from backend.memory.manager import MemoryManager
from backend.memory.page import VirtualPage


# =============================================================================
# 1. Binding and Sequence Initialization
# =============================================================================

def test_opt_binding_pristine_success():
    """1. Successful pristine OPT binding sets _is_integrated = True and normalizes sequence."""
    seq = [1, 2, 3]
    opt = OptimalCache(3, reference_sequence=seq)
    assert not opt._is_integrated
    assert opt.state_version == 0

    mm = MemoryManager(capacity=3, algorithm=opt)
    assert opt._is_integrated is True
    assert mm.policy.name == "OptimalCache"
    assert opt.state_version == 0
    assert opt.sequence == [VirtualPage(1, 1), VirtualPage(1, 2), VirtualPage(1, 3)]


def test_opt_rebinding_and_non_pristine_rejected():
    """2. Rejection of non-pristine OPT binding and rebinding (BindingError)."""
    # Non-pristine after request
    opt1 = OptimalCache(3, [1, 2, 3])
    opt1.request(1)
    with pytest.raises(BindingError) as exc_req:
        MemoryManager(3, algorithm=opt1)
    assert "non-pristine" in str(exc_req.value)

    # Non-pristine step
    opt2 = OptimalCache(3, [1, 2, 3])
    opt2.current_step = 1
    with pytest.raises(BindingError) as exc_step:
        opt2.bind_to_manager()
    assert "non-pristine" in str(exc_step.value)

    # Rebinding already bound
    opt3 = OptimalCache(3, [1, 2, 3])
    mm = MemoryManager(3, algorithm=opt3)
    with pytest.raises(BindingError) as exc_rebind:
        MemoryManager(3, algorithm=opt3)
    assert "already bound" in str(exc_rebind.value)

    with pytest.raises(BindingError):
        opt3.bind_to_manager()


def test_opt_missing_reference_sequence_rejected():
    """3. Missing reference sequence raises ValueError."""
    # By string without sequence
    with pytest.raises(ValueError) as exc_str:
        MemoryManager(3, algorithm="OPT")
    assert "reference_sequence" in str(exc_str.value)

    # By instance without sequence
    opt = OptimalCache(3)
    with pytest.raises(ValueError) as exc_inst:
        MemoryManager(3, algorithm=opt)
    assert "reference sequence" in str(exc_inst.value)

    with pytest.raises(ValueError):
        opt.bind_to_manager()


def test_opt_correct_sequence_initialization_process_identity():
    """4. Correct sequence initialization translates while preserving process identity."""
    seq = [
        VirtualPage(1, 10),
        VirtualPage(2, 10),
        (1, 20),
        30
    ]
    mm = MemoryManager(capacity=3, algorithm="OPT", reference_sequence=seq)
    opt = mm.policy.policy

    assert opt.sequence[0] == VirtualPage(1, 10)
    assert opt.sequence[1] == VirtualPage(2, 10)
    assert opt.sequence[2] == VirtualPage(1, 20)
    assert opt.sequence[3] == VirtualPage(1, 30)

    # Distinct processes with same VPN are distinct
    assert opt.sequence[0] != opt.sequence[1]
    assert list(opt.page_to_future_indices[VirtualPage(1, 10)]) == [0]
    assert list(opt.page_to_future_indices[VirtualPage(2, 10)]) == [1]


# =============================================================================
# 2. Request Sequencing and Validation
# =============================================================================

def test_opt_correct_expected_page_validation():
    """5. Correct expected-page validation allows sequence references to execute."""
    seq = [VirtualPage(1, 10), VirtualPage(1, 20)]
    mm = MemoryManager(capacity=2, algorithm="OPT", reference_sequence=seq)

    r1 = mm.access_page(1, 10)
    assert r1.hit is False
    assert r1.frame_id == 0

    r2 = mm.access_page(1, 20)
    assert r2.hit is False
    assert r2.frame_id == 1

    assert mm.policy.policy.current_step == 2


def test_opt_unexpected_page_rejection_without_state_mutation():
    """6. Unexpected-page rejection raises ValueError with zero state mutation."""
    seq = [10, 20, 30]
    mm = MemoryManager(capacity=2, algorithm="OPT", reference_sequence=seq)
    opt = mm.policy.policy

    # Access step 0 (page 10) successfully
    mm.access_page(1, 10)
    assert opt.current_step == 1

    # Snapshot state prior to error
    step_before = opt.current_step
    version_before = opt.state_version
    cache_before = dict(opt.cache)
    queues_before = {k: list(v) for k, v in opt.page_to_future_indices.items()}

    # Request unexpected page 99 (expected 20)
    with pytest.raises(ValueError) as excinfo:
        mm.access_page(1, 99)
    assert "Sequence mismatch at index 1" in str(excinfo.value)
    assert "expected page" in str(excinfo.value)

    # Zero state mutation verified
    assert opt.current_step == step_before
    assert opt.state_version == version_before
    assert opt.cache == cache_before
    assert {k: list(v) for k, v in opt.page_to_future_indices.items()} == queues_before
    assert mm.is_consistent is True

    # Resuming with the correct expected page succeeds
    res_correct = mm.access_page(1, 20)
    assert res_correct.hit is False
    assert opt.current_step == 2


def test_opt_sequence_exhaustion_without_state_mutation():
    """7. Sequence exhaustion raises ValueError without mutating state."""
    seq = [1, 2]
    mm = MemoryManager(capacity=2, algorithm="OPT", reference_sequence=seq)
    opt = mm.policy.policy

    mm.access_page(1, 1)
    mm.access_page(1, 2)
    assert opt.current_step == 2

    step_before = opt.current_step
    version_before = opt.state_version
    cache_before = dict(opt.cache)

    # Access beyond sequence length
    with pytest.raises(ValueError) as excinfo:
        mm.access_page(1, 1)
    assert "Reference sequence exhausted" in str(excinfo.value)

    assert opt.current_step == step_before
    assert opt.state_version == version_before
    assert opt.cache == cache_before
    assert mm.is_consistent is True


# =============================================================================
# 3. Victim Selection and Tie-Breaking
# =============================================================================

def test_opt_optimal_victim_selection_furthest_future():
    """8. Optimal victim selection evicts the page with the furthest future reference."""
    # Sequence: 1, 2, 3, 4, 1, 2, 5
    # At step 3 (page 4):
    # Cache contains:
    # 1 -> next at step 4 (dist = 1)
    # 2 -> next at step 5 (dist = 2)
    # 3 -> never referenced again (dist = inf)
    # Victim is 3!
    seq = [1, 2, 3, 4, 1, 2, 5]
    mm = MemoryManager(capacity=3, algorithm="OPT", reference_sequence=seq)

    mm.access_page(1, 1)  # Frame 0
    mm.access_page(1, 2)  # Frame 1
    mm.access_page(1, 3)  # Frame 2

    # Miss with eviction
    r4 = mm.access_page(1, 4)
    assert r4.hit is False
    assert r4.evicted_page == VirtualPage(1, 3)
    assert r4.frame_id == 2


def test_opt_never_used_again_page_selection():
    """9. Never-used-again page (distance = infinity) is prioritized as victim."""
    # Sequence: 1, 2, 3, 4, 1, 3
    # At step 3: page 2 is never used again (dist = inf), while 1 and 3 are referenced later
    seq = [1, 2, 3, 4, 1, 3]
    mm = MemoryManager(capacity=3, algorithm="OPT", reference_sequence=seq)

    mm.access_page(1, 1)
    mm.access_page(1, 2)
    mm.access_page(1, 3)

    r4 = mm.access_page(1, 4)
    assert r4.evicted_page == VirtualPage(1, 2)


def test_opt_deterministic_tie_breaking():
    """10. Deterministic FIFO tie-breaking for pages with equal future distance / infinity."""
    # Sequence: 1, 2, 3, 4
    # All 3 pages (1, 2, 3) are never referenced again.
    # Page 1 arrived first (step 0), so it must be evicted first.
    seq = [1, 2, 3, 4]
    mm = MemoryManager(capacity=3, algorithm="OPT", reference_sequence=seq)

    mm.access_page(1, 1)  # step 0
    mm.access_page(1, 2)  # step 1
    mm.access_page(1, 3)  # step 2

    r4 = mm.access_page(1, 4)
    assert r4.evicted_page == VirtualPage(1, 1)
    assert r4.frame_id == 0


# =============================================================================
# 4. Token Planning and Immutability
# =============================================================================

def test_opt_non_mutating_victim_planning():
    """11. Non-mutating victim planning in Phase 1 causes zero state mutations."""
    seq = [1, 2, 3]
    mm = MemoryManager(capacity=2, algorithm="OPT", reference_sequence=seq)
    mm.access_page(1, 1)
    mm.access_page(1, 2)

    opt = mm.policy.policy
    step_before = opt.current_step
    version_before = opt.state_version
    cache_before = dict(opt.cache)
    queues_before = {k: list(v) for k, v in opt.page_to_future_indices.items()}

    # Phase 1 planning
    val_token = mm.policy.validate_request(VirtualPage(1, 3))
    victim_token = mm.policy.select_victim(token=val_token)

    assert isinstance(victim_token, OptVictimToken)
    assert victim_token.victim_page == VirtualPage(1, 1)

    # State untouched
    assert opt.current_step == step_before
    assert opt.state_version == version_before
    assert opt.cache == cache_before
    assert {k: list(v) for k, v in opt.page_to_future_indices.items()} == queues_before

    # Clean abort
    mm.policy.abort_request(val_token)


def test_opt_stale_token_rejected_after_sequence_mutation():
    """12. Stale token rejected after sequence advancement (StaleTokenError)."""
    seq = [1, 2, 3, 4]
    mm = MemoryManager(capacity=2, algorithm="OPT", reference_sequence=seq)
    mm.access_page(1, 1)
    mm.access_page(1, 2)

    val_token = mm.policy.validate_request(VirtualPage(1, 3))
    victim_token = mm.policy.select_victim(token=val_token)

    # Intervening sequence advance
    mm.policy.policy.current_step += 1

    with pytest.raises(StaleTokenError) as exc_info:
        mm.policy.evict_victim(victim_token.victim_page, victim_token)
    assert "Stale" in str(exc_info.value)

    mm.policy.abort_request(val_token)


def test_opt_stale_token_rejected_after_resident_mapping_mutation():
    """13. Stale token rejected after resident mapping mutation (StaleTokenError)."""
    seq = [1, 2, 3]
    mm = MemoryManager(capacity=2, algorithm="OPT", reference_sequence=seq)
    mm.access_page(1, 1)
    mm.access_page(1, 2)

    val_token = mm.policy.validate_request(VirtualPage(1, 3))
    victim_token = mm.policy.select_victim(token=val_token)

    # Intervening cache mutation
    mm.policy.policy.cache[VirtualPage(1, 99)] = 99

    with pytest.raises(StaleTokenError) as exc_info:
        mm.policy.evict_victim(victim_token.victim_page, victim_token)
    assert "Stale" in str(exc_info.value)

    mm.policy.abort_request(val_token)


# =============================================================================
# 5. Physical Memory and Backing Store Lifecycle
# =============================================================================

def test_opt_free_frame_allocation_before_eviction():
    """14. Free frames allocated without evicting resident pages."""
    seq = [10, 20, 30, 40]
    mm = MemoryManager(capacity=4, algorithm="OPT", reference_sequence=seq)

    r1 = mm.access_page(1, 10)
    r2 = mm.access_page(1, 20)
    r3 = mm.access_page(1, 30)

    assert mm.allocator.has_free_frame() is True
    assert mm.metrics.successful_evictions == 0

    r4 = mm.access_page(1, 40)
    assert r4.frame_id == 3
    assert r4.evicted_page is None
    assert mm.metrics.successful_evictions == 0


def test_opt_successful_dirty_victim_write_back():
    """15. Successful dirty victim write-back persists to backing store."""
    seq = [1, 2, 3]
    mm = MemoryManager(capacity=2, algorithm="OPT", reference_sequence=seq)

    # Page 1 write
    mm.access_page(1, 1, operation="WRITE", write_data="p1_data")
    mm.access_page(1, 2, operation="READ")

    # Evicts page 1 (dirty)
    r3 = mm.access_page(1, 3, operation="READ")
    assert r3.hit is False
    assert r3.evicted_page == VirtualPage(1, 1)
    assert r3.write_back_occurred is True

    # Backing store persisted data
    assert mm.backing_store.read_page(VirtualPage(1, 1)) == "p1_data"

    # Frame 0 holds clean page 3
    f0 = mm.get_frame(0)
    assert f0.vpage == VirtualPage(1, 3)
    assert f0.dirty is False


def test_opt_failed_write_back_preserves_victim_state():
    """16. Failed write-back preserves victim state, dirty bit, and OPT step."""
    seq = [1, 2, 3]
    mm = MemoryManager(capacity=2, algorithm="OPT", reference_sequence=seq)

    mm.access_page(1, 1, operation="WRITE", write_data="dirty1")
    mm.access_page(1, 2, operation="WRITE", write_data="dirty2")

    opt = mm.policy.policy
    step_before = opt.current_step

    # Fail write-back in Phase 2
    with patch.object(mm.backing_store, "write_page", side_effect=IOError("Disk fault")):
        with pytest.raises(DiskWriteError) as exc_info:
            mm.access_page(1, 3)
        assert "Failed write-back" in str(exc_info.value)

    # State preservation
    f0 = mm.get_frame(0)
    assert f0.vpage == VirtualPage(1, 1)
    assert f0.dirty is True
    assert opt.current_step == step_before
    assert mm.is_consistent is True


def test_opt_failed_page_in_preserves_opt_sequence_position():
    """17. Failed page-in preserves OPT sequence position and victim residency."""
    seq = [1, 2, 3]
    mm = MemoryManager(capacity=2, algorithm="OPT", reference_sequence=seq)

    mm.access_page(1, 1, operation="READ")
    mm.access_page(1, 2, operation="READ")

    opt = mm.policy.policy
    step_before = opt.current_step

    with patch.object(mm.backing_store, "read_page", side_effect=IOError("Read fault")):
        with pytest.raises(DiskReadError) as exc_info:
            mm.access_page(1, 3)
        assert "Failed to read page" in str(exc_info.value)

    f0 = mm.get_frame(0)
    assert f0.vpage == VirtualPage(1, 1)
    assert opt.current_step == step_before
    assert mm.is_consistent is True


# =============================================================================
# 6. Step Advancement and Metrics Accounting
# =============================================================================

def test_opt_successful_hit_advances_sequence_exactly_once():
    """18. Successful hit advances sequence step and updates hits metrics."""
    seq = [1, 2, 1, 3]
    mm = MemoryManager(capacity=2, algorithm="OPT", reference_sequence=seq)
    opt = mm.policy.policy

    mm.access_page(1, 1)  # step 0 -> 1
    mm.access_page(1, 2)  # step 1 -> 2

    # Step 2: Hit on page 1
    r_hit = mm.access_page(1, 1)
    assert r_hit.hit is True
    assert opt.current_step == 3
    assert opt.hits == 1
    assert opt.requests == 3
    assert mm.metrics.hits == 1


def test_opt_successful_miss_advances_sequence_exactly_once():
    """19. Successful miss advances sequence step and updates misses/eviction metrics."""
    seq = [1, 2, 1, 3]
    mm = MemoryManager(capacity=2, algorithm="OPT", reference_sequence=seq)
    opt = mm.policy.policy

    mm.access_page(1, 1)
    mm.access_page(1, 2)
    mm.access_page(1, 1)

    # Step 3: Miss on page 3 (evicts 2, since 1 is never used again either but 2 arrived later... wait!)
    # Future occurrences:
    # At step 3:
    # 1: never used again
    # 2: never used again
    # FIFO tie-break: 1 arrived at step 0, 2 arrived at step 1.
    # Page 1 is evicted!
    r_miss = mm.access_page(1, 3)
    assert r_miss.hit is False
    assert r_miss.evicted_page == VirtualPage(1, 1)
    assert opt.current_step == 4
    assert opt.misses == 3
    assert opt.requests == 4
    assert mm.metrics.successful_evictions == 1


# =============================================================================
# 7. Reset and Standalone Invariants
# =============================================================================

def test_opt_coordinated_reset_preserves_integrated_mode():
    """20. Coordinated reset resets OPT sequence to start, monotonically increments version, keeps _is_integrated."""
    seq = [1, 2, 3, 1]
    mm = MemoryManager(capacity=3, algorithm="OPT", reference_sequence=seq)
    opt = mm.policy.policy

    mm.access_page(1, 1)
    mm.access_page(1, 2)
    mm.access_page(1, 3)

    version_before = opt.state_version
    assert opt._is_integrated is True

    mm.reset()

    assert opt._is_integrated is True
    assert opt.state_version > version_before
    assert opt.current_step == 0
    assert len(opt.cache) == 0
    assert mm.is_consistent is True

    # Re-run same workload from step 0
    r1 = mm.access_page(1, 1)
    assert r1.hit is False
    assert opt.current_step == 1


def test_standalone_opt_backward_compatibility():
    """21. Standalone OPTCache behavior and methods remain backward-compatible."""
    opt = OptimalCache(capacity=2, reference_sequence=[1, 2, 3, 1, 2])
    assert not opt._is_integrated

    r1 = opt.request(1)
    assert r1["hit"] is False
    assert r1["evicted_page"] is None

    r2 = opt.request(2)
    r3 = opt.request(3)  # evicts 1 (next at 3 vs 2 next at 4 -> 1 is earlier, so 2 is evicted? Wait: 1 next at 3, 2 next at 4 -> 2 is furthest!)
    assert r3["hit"] is False
    assert r3["evicted_page"] == 2

    opt.reset()
    assert opt.current_step == 0
    assert opt.requests == 0
    assert not opt._is_integrated


def test_opt_commit_unexpected_failure_and_panic_state():
    """22. Unexpected commit failure marks manager inconsistent and blocks accesses."""
    seq = [1, 2, 3]
    mm = MemoryManager(capacity=2, algorithm="OPT", reference_sequence=seq)
    mm.access_page(1, 1)

    # Patch policy insert_page to raise unexpected error during Phase 3
    with patch.object(mm.policy, "insert_page", side_effect=RuntimeError("Hardware fault")):
        with pytest.raises(CorruptedManagerStateError) as exc_info:
            mm.access_page(1, 2)
        assert "Fatal commit failure" in str(exc_info.value)

    assert mm.is_consistent is False

    with pytest.raises(CorruptedManagerStateError) as exc_blocked:
        mm.access_page(1, 3)
    assert "inconsistent panic state" in str(exc_blocked.value)

