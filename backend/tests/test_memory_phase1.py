"""
Unit tests for Stage 12.2 Phase 1: Virtual Memory Structures.
Tests:
  - VirtualPage: immutability, validation, identity, hashing, ordering.
  - PhysicalFrame: encapsulation, read-only bits, controlled mutation, page lifecycle.
  - PageTable & PageTableEntry: mappings, unmapping, residency tracking, reset.
  - FrameAllocator: FIFO allocation, capacity bounds, double-free rejection, reclamation.
"""
from dataclasses import FrozenInstanceError
import pytest

from backend.memory.allocator import FrameAllocator
from backend.memory.exceptions import (
    DoubleFreeError,
    InvalidAddressError,
    InvalidFrameError,
    NoFreeFrameError,
)
from backend.memory.frame import PhysicalFrame
from backend.memory.page import VirtualPage
from backend.memory.page_table import PageTable, PageTableEntry


# =============================================================================
# 1. VirtualPage Tests
# =============================================================================

def test_virtual_page_initialization_and_identity():
    """Verify VirtualPage creation, attributes, and composite page_id."""
    vp = VirtualPage(pid=1, vpn=42)
    assert vp.pid == 1
    assert vp.vpn == 42
    assert vp.page_id == (1, 42)
    assert str(vp) == "P1:VPN42"
    assert repr(vp) == "VirtualPage(pid=1, vpn=42)"


def test_virtual_page_equality_and_hashing():
    """Verify value equality and hashability for sets and dict keys."""
    vp1 = VirtualPage(pid=0, vpn=5)
    vp2 = VirtualPage(pid=0, vpn=5)
    vp3 = VirtualPage(pid=1, vpn=5)
    vp4 = VirtualPage(pid=0, vpn=6)

    assert vp1 == vp2
    assert hash(vp1) == hash(vp2)
    assert vp1 != vp3
    assert vp1 != vp4

    page_set = {vp1, vp2, vp3}
    assert len(page_set) == 2
    assert vp1 in page_set
    assert vp3 in page_set

    page_dict = {vp1: "data_for_p0_v5"}
    assert page_dict[vp2] == "data_for_p0_v5"


def test_virtual_page_immutability():
    """Verify VirtualPage is strictly immutable (frozen)."""
    vp = VirtualPage(pid=2, vpn=10)
    with pytest.raises(FrozenInstanceError):
        vp.pid = 3  # type: ignore
    with pytest.raises(FrozenInstanceError):
        vp.vpn = 11  # type: ignore


def test_virtual_page_ordering():
    """Verify sorting order is deterministic (by pid, then vpn)."""
    pages = [
        VirtualPage(pid=2, vpn=0),
        VirtualPage(pid=1, vpn=10),
        VirtualPage(pid=1, vpn=2),
        VirtualPage(pid=0, vpn=5),
    ]
    sorted_pages = sorted(pages)
    expected = [
        VirtualPage(pid=0, vpn=5),
        VirtualPage(pid=1, vpn=2),
        VirtualPage(pid=1, vpn=10),
        VirtualPage(pid=2, vpn=0),
    ]
    assert sorted_pages == expected


def test_virtual_page_validation():
    """Verify rejection of invalid, negative, or non-integer addresses."""
    with pytest.raises(InvalidAddressError):
        VirtualPage(pid=-1, vpn=0)
    with pytest.raises(InvalidAddressError):
        VirtualPage(pid=0, vpn=-5)
    with pytest.raises(InvalidAddressError):
        VirtualPage(pid="0", vpn=1)  # type: ignore
    with pytest.raises(InvalidAddressError):
        VirtualPage(pid=1, vpn="2")  # type: ignore
    with pytest.raises(InvalidAddressError):
        VirtualPage(pid=True, vpn=1)  # type: ignore
    with pytest.raises(InvalidAddressError):
        VirtualPage(pid=1, vpn=False)  # type: ignore


# =============================================================================
# 2. PhysicalFrame Tests
# =============================================================================

def test_physical_frame_initialization():
    """Verify initial state of an unallocated PhysicalFrame."""
    frame = PhysicalFrame(frame_id=0)
    assert frame.frame_id == 0
    assert frame.vpage is None
    assert frame.is_free is True
    assert frame.is_allocated is False
    assert frame.reference_bit is False
    assert frame.dirty is False
    assert frame.data is None
    assert "FREE" in str(frame)


def test_physical_frame_id_validation():
    """Verify validation of frame identifiers."""
    with pytest.raises(InvalidFrameError):
        PhysicalFrame(frame_id=-1)
    with pytest.raises(InvalidFrameError):
        PhysicalFrame(frame_id="0")  # type: ignore
    with pytest.raises(InvalidFrameError):
        PhysicalFrame(frame_id=True)  # type: ignore


def test_physical_frame_read_only_properties():
    """Verify that reference_bit and dirty are strictly read-only for external callers."""
    frame = PhysicalFrame(frame_id=1)
    with pytest.raises(AttributeError):
        frame.reference_bit = True  # type: ignore
    with pytest.raises(AttributeError):
        frame.dirty = True  # type: ignore


def test_physical_frame_controlled_mutation():
    """Verify internal controlled mutation methods work as specified."""
    frame = PhysicalFrame(frame_id=2)
    frame._set_reference_bit(True)
    assert frame.reference_bit is True
    frame._set_reference_bit(False)
    assert frame.reference_bit is False

    frame._set_dirty(True)
    assert frame.dirty is True
    frame._set_dirty(False)
    assert frame.dirty is False


def test_physical_frame_assign_and_clear_lifecycle():
    """Verify page loading, occupancy flags, and clearing."""
    frame = PhysicalFrame(frame_id=3)
    vp = VirtualPage(pid=1, vpn=7)

    # Assign page
    frame.assign_page(vpage=vp, dirty=False, reference=True, data="test_payload")
    assert frame.is_free is False
    assert frame.is_allocated is True
    assert frame.vpage == vp
    assert frame.reference_bit is True
    assert frame.dirty is False
    assert frame.data == "test_payload"
    assert "P1:VPN7" in str(frame)

    # Re-assign with dirty
    frame.assign_page(vpage=vp, dirty=True, reference=True, data="modified_payload")
    assert frame.dirty is True
    assert frame.data == "modified_payload"

    # Clear frame
    frame.clear_frame()
    assert frame.is_free is True
    assert frame.is_allocated is False
    assert frame.vpage is None
    assert frame.reference_bit is False
    assert frame.dirty is False
    assert frame.data is None

    # Invalid assignment
    with pytest.raises(TypeError):
        frame.assign_page(vpage="not_a_virtual_page")  # type: ignore


# =============================================================================
# 3. PageTable & PageTableEntry Tests
# =============================================================================

def test_page_table_entry_initialization_and_validation():
    """Verify PageTableEntry attributes and validation."""
    pte = PageTableEntry(vpn=10, frame_id=2, valid=True, read_only=False)
    assert pte.vpn == 10
    assert pte.frame_id == 2
    assert pte.valid is True
    assert pte.read_only is False

    with pytest.raises(InvalidAddressError):
        PageTableEntry(vpn=-1)
    with pytest.raises(InvalidFrameError):
        PageTableEntry(vpn=0, frame_id=-2)


def test_page_table_lifecycle():
    """Verify PageTable entry retrieval, mapping, unmapping, and queries."""
    pt = PageTable(pid=1)
    assert pt.pid == 1
    assert len(pt) == 0
    assert pt.get_resident_pages() == []

    # Get entry for unaccessed VPN allocates invalid PTE
    entry = pt.get_entry(vpn=0)
    assert entry.vpn == 0
    assert entry.valid is False
    assert entry.frame_id is None
    assert len(pt) == 1
    assert pt.is_valid(0) is False
    assert pt.get_frame_id(0) is None
    assert 0 not in pt

    # Map page
    mapped_entry = pt.map_page(vpn=0, frame_id=5, read_only=True)
    assert mapped_entry.valid is True
    assert mapped_entry.frame_id == 5
    assert mapped_entry.read_only is True
    assert pt.is_valid(0) is True
    assert pt.get_frame_id(0) == 5
    assert 0 in pt
    assert pt.get_resident_pages() == [0]

    # Map second page
    pt.map_page(vpn=4, frame_id=2)
    assert pt.get_resident_pages() == [0, 4]

    # Unmap page
    unmapped = pt.unmap_page(vpn=0)
    assert unmapped is not None
    assert unmapped.valid is False
    assert unmapped.frame_id is None
    assert pt.is_valid(0) is False
    assert pt.get_resident_pages() == [4]

    # Unmap non-existent page returns None
    assert pt.unmap_page(vpn=99) is None

    # Reset
    pt.reset()
    assert len(pt) == 0
    assert pt.get_resident_pages() == []


def test_page_table_validation():
    """Verify PageTable parameter constraints."""
    with pytest.raises(InvalidAddressError):
        PageTable(pid=-1)
    with pytest.raises(InvalidAddressError):
        PageTable(pid="1")  # type: ignore

    pt = PageTable(pid=0)
    with pytest.raises(InvalidAddressError):
        pt.get_entry(vpn=-1)
    with pytest.raises(InvalidFrameError):
        pt.map_page(vpn=1, frame_id=-1)


# =============================================================================
# 4. FrameAllocator Tests
# =============================================================================

def test_frame_allocator_initialization_and_capacity():
    """Verify FrameAllocator initial capacity, free counts, and validation."""
    allocator = FrameAllocator(capacity=4)
    assert allocator.capacity == 4
    assert len(allocator) == 4
    assert allocator.free_count == 4
    assert allocator.allocated_count == 0
    assert allocator.has_free_frame() is True
    assert allocator.peek_free_frame() == 0
    assert allocator.get_free_frames() == [0, 1, 2, 3]
    assert allocator.get_allocated_frames() == set()

    with pytest.raises(ValueError):
        FrameAllocator(capacity=0)
    with pytest.raises(ValueError):
        FrameAllocator(capacity=-3)
    with pytest.raises(ValueError):
        FrameAllocator(capacity="4")  # type: ignore
    with pytest.raises(ValueError):
        FrameAllocator(capacity=True)  # type: ignore


def test_frame_allocator_fifo_allocation_and_exhaustion():
    """Verify FIFO frame allocation order and NoFreeFrameError on exhaustion."""
    allocator = FrameAllocator(capacity=3)

    f0 = allocator.allocate_frame()
    assert f0 == 0
    assert allocator.is_allocated(0) is True
    assert allocator.is_free(0) is False
    assert allocator.free_count == 2
    assert allocator.allocated_count == 1
    assert allocator.peek_free_frame() == 1

    f1 = allocator.allocate_frame()
    assert f1 == 1

    f2 = allocator.allocate_frame()
    assert f2 == 2

    assert allocator.free_count == 0
    assert allocator.allocated_count == 3
    assert allocator.has_free_frame() is False
    assert allocator.peek_free_frame() is None

    # Exhaustion
    with pytest.raises(NoFreeFrameError):
        allocator.allocate_frame()


def test_frame_allocator_free_and_reuse():
    """Verify releasing frames, FIFO reclamation order, and reuse."""
    allocator = FrameAllocator(capacity=3)
    f0 = allocator.allocate_frame()  # 0
    f1 = allocator.allocate_frame()  # 1
    f2 = allocator.allocate_frame()  # 2

    # Free frame 1
    allocator.free_frame(1)
    assert allocator.is_allocated(1) is False
    assert allocator.is_free(1) is True
    assert allocator.free_count == 1
    assert allocator.allocated_count == 2
    assert allocator.peek_free_frame() == 1

    # Free frame 0
    allocator.free_frame(0)
    assert allocator.free_count == 2
    assert allocator.allocated_count == 1
    # Frames in free list should be [1, 0] in order of release
    assert allocator.get_free_frames() == [1, 0]

    # Re-allocate: should receive frame 1 first, then frame 0
    next_f = allocator.allocate_frame()
    assert next_f == 1
    next_f2 = allocator.allocate_frame()
    assert next_f2 == 0


def test_frame_allocator_double_free_rejection():
    """Verify rejection of double-free operations."""
    allocator = FrameAllocator(capacity=2)
    f0 = allocator.allocate_frame()

    allocator.free_frame(f0)
    with pytest.raises(DoubleFreeError):
        allocator.free_frame(f0)

    # Frame 1 was never allocated
    with pytest.raises(DoubleFreeError):
        allocator.free_frame(1)


def test_frame_allocator_out_of_bounds_validation():
    """Verify boundary checking on free_frame."""
    allocator = FrameAllocator(capacity=3)
    with pytest.raises(InvalidFrameError):
        allocator.free_frame(-1)
    with pytest.raises(InvalidFrameError):
        allocator.free_frame(3)  # Out of range [0, 2]
    with pytest.raises(InvalidFrameError):
        allocator.free_frame("1")  # type: ignore


def test_frame_allocator_reset():
    """Verify reset restores the allocator to clean initial state."""
    allocator = FrameAllocator(capacity=3)
    allocator.allocate_frame()
    allocator.allocate_frame()
    assert allocator.allocated_count == 2

    allocator.reset()
    assert allocator.free_count == 3
    assert allocator.allocated_count == 0
    assert allocator.get_free_frames() == [0, 1, 2]
    assert allocator.get_allocated_frames() == set()

