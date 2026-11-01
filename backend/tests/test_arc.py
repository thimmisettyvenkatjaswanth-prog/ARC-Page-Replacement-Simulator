"""
Unit and invariant test suite for Adaptive Replacement Cache (ARC).
Rigorously verifies compliance with Megiddo & Modha (USENIX FAST 2003).
"""
import pytest
import random
from backend.algorithms.arc import ARCCache


def test_capacity_validation():
    """Verify capacity parameter constraints."""
    with pytest.raises(ValueError):
        ARCCache(0)
    with pytest.raises(ValueError):
        ARCCache(-5)
    with pytest.raises(ValueError):
        ARCCache("invalid")  # type: ignore

    arc = ARCCache(4)
    assert arc.capacity == 4
    assert arc.p == 0.0
    assert isinstance(arc.p, float)


def test_warmup_and_case_4b():
    """Verify initial warmup inserts pages to T1 (MRU)."""
    arc = ARCCache(3)
    res1 = arc.request("A")
    assert res1["hit"] is False
    assert res1["case"] == "CASE_4B_L1_LESS"
    assert arc.get_state()["T1"] == ["A"]

    res2 = arc.request("B")
    assert res2["hit"] is False
    assert arc.get_state()["T1"] == ["B", "A"]  # MRU -> LRU

    res3 = arc.request("C")
    assert res3["hit"] is False
    assert arc.get_state()["T1"] == ["C", "B", "A"]
    assert arc.get_state()["size_cache"] == 3


def test_case_1_hit_promotion():
    """
    Case 1: Hit in T1 moves to T2 MRU. Hit in T2 moves to T2 MRU.
    """
    arc = ARCCache(3)
    arc.request("A")
    arc.request("B")
    arc.request("C")
    # Current: T1 = [C, B, A], T2 = []

    # Hit on B (currently in T1)
    res = arc.request("B")
    assert res["hit"] is True
    assert res["hit_type"] == "T1_HIT"
    assert res["case"] == "CASE_1_T1_HIT"
    state = arc.get_state()
    assert state["T1"] == ["C", "A"]
    assert state["T2"] == ["B"]

    # Hit on B again (now in T2)
    res2 = arc.request("B")
    assert res2["hit"] is True
    assert res2["hit_type"] == "T2_HIT"
    assert res2["case"] == "CASE_1_T2_HIT"
    assert arc.get_state()["T2"] == ["B"]


def test_case_4a_eviction_when_l1_full():
    """
    When |L1| == c and |T1| == c, LRU page in T1 is evicted.
    """
    arc = ARCCache(2)
    arc.request("A")
    arc.request("B")
    # T1 = [B, A], |L1| == 2 == c, |T1| == 2 == c

    res = arc.request("C")
    assert res["hit"] is False
    assert res["case"] == "CASE_4A_L1_FULL"
    assert res["evicted_page"] == "A"
    assert res["eviction_type"] == "T1_EVICT"
    state = arc.get_state()
    assert state["T1"] == ["C", "B"]


def test_ghost_b1_hit_and_p_increase():
    """
    Case 2: Hit in B1 increases p by delta1 = max(1.0, |B2| / |B1|), calls REPLACE,
    and moves page to T2 (MRU).
    """
    arc = ARCCache(2)
    arc.request(1)
    arc.request(2)
    # T1 = [2, 1]

    # Promote 2 to T2: T1 = [1], T2 = [2]
    arc.request(2)

    # Request 3: |L1| < c (|L1|=1), total |L1|+|L2| = 2 >= c -> REPLACE called.
    # REPLACE: |T1| = 1, p = 0.0 -> |T1| > p, so LRU of T1 (1) moves to B1.
    # Result: T1 = [3], T2 = [2], B1 = [1]
    res3 = arc.request(3)
    assert res3["eviction_type"] == "T1->B1"
    assert res3["evicted_page"] == 1
    state = arc.get_state()
    assert 1 in state["B1"]
    assert arc.p == 0.0

    # Request 1: Hit in B1!
    res1 = arc.request(1)
    assert res1["hit"] is False
    assert res1["hit_type"] == "B1_GHOST_HIT"
    assert res1["case"] == "CASE_2_B1_HIT"
    # |B1| = 1, |B2| = 0 -> delta = 1.0 -> p becomes min(2.0, 0.0 + 1.0) = 1.0
    assert arc.p == 1.0
    assert 1 in arc.get_state()["T2"]
    assert 1 not in arc.get_state()["B1"]


def test_ghost_b2_hit_and_p_decrease():
    """
    Case 3: Hit in B2 decreases p by delta2 = max(1.0, |B1| / |B2|), calls REPLACE,
    and moves page to T2 (MRU).
    """
    arc = ARCCache(3)
    # Manually configure state to test B2 hit with non-zero p
    arc.p = 2.0
    arc.T2[1] = None
    arc.T2[2] = None
    arc.B2[3] = None
    arc.B1[4] = None
    # |B1| = 1, |B2| = 1

    res = arc.request(3)
    assert res["hit_type"] == "B2_GHOST_HIT"
    assert res["case"] == "CASE_3_B2_HIT"
    # delta = max(1.0, 1.0 / 1.0) = 1.0 -> p decreases from 2.0 to 1.0
    assert arc.p == 1.0
    assert 3 in arc.get_state()["T2"]
    assert 3 not in arc.get_state()["B2"]


def test_replace_special_b2_condition():
    """
    Verify the REPLACE condition:
    if (|T1| >= 1 and ((x_t in B2 and |T1| == p) or (|T1| > p))) -> evict T1 else T2

    Deterministic test for the equality branch (|T1| == p when page in B2):
    At the moment _replace() is executed inside request(99):
      - |T1| == 2, |T2| == 2, p == 2.0
      - |T1| > p is FALSE (2 > 2.0 is False)
      - page in B2 and |T1| == p is TRUE (99 in B2 and 2 == 2.0)
    
    If the special B2 condition were omitted, _replace() would evaluate to False
    and evict from T2 (page 20). With the special condition, it correctly evicts from T1 (page 10).
    """
    arc = ARCCache(4)
    # In Case 3, p is decremented by delta2 before _replace() is called.
    # With |B1| = 0 and |B2| = 1, delta2 = 1.0.
    # Setting initial p = 3.0 ensures that after adaptation: p = 3.0 - 1.0 = 2.0 == len(T1).
    arc.p = 3.0
    arc.T1[10] = None
    arc.T1[11] = None  # |T1| = 2, LRU is 10
    arc.T2[20] = None
    arc.T2[21] = None  # |T2| = 2, LRU is 20
    arc.B2[99] = None  # |B2| = 1, page 99 in B2

    res = arc.request(99)
    # Verifies eviction occurred from T1 due strictly to the equality condition
    assert res["eviction_type"] == "T1->B1"
    assert res["evicted_page"] == 10
    st = arc.get_state()
    assert 10 in st["B1"]
    assert 20 in st["T2"]


def test_replace_special_b2_condition_direct():
    """
    Direct invocation test of _replace() isolating the equality condition.
    Explicitly tests: len(T1) == p (2 == 2.0), len(T1) > p is False, page in B2.
    """
    arc = ARCCache(4)
    arc.p = 2.0
    arc.T1[10] = None
    arc.T1[11] = None  # len(T1) = 2 == p
    arc.T2[20] = None
    arc.T2[21] = None  # len(T2) = 2
    arc.B2[99] = None  # 99 in B2

    # Directly call _replace
    eviction_type, evicted = arc._replace(99)
    assert eviction_type == "T1->B1"
    assert evicted == 10


def test_replace_with_fractional_p_does_not_round():
    """
    Verify REPLACE behavior when p is fractional:
    Condition: |T1| >= 1 and ((page in B2 and |T1| == p) or (|T1| > p))
    When p is fractional (e.g. 5/3 = 1.6667):
    - If |T1| == 2: |T1| > p (2 > 1.6667) is True -> evicts T1.
    - If |T1| == 1: |T1| > p (1 > 1.6667) is False, and 1 == 1.6667 is False -> evicts T2.
    Verifies that no silent rounding of p takes place.
    """
    # Scenario A: len(T1) = 2 > p = 1.6667 -> evicts T1
    arc1 = ARCCache(4)
    arc1.p = 5.0 / 3.0  # ~1.6667
    arc1.T1[10] = None; arc1.T1[11] = None  # len(T1) = 2
    arc1.T2[20] = None; arc1.T2[21] = None  # len(T2) = 2
    arc1.B2[99] = None
    ev_type1, ev_page1 = arc1._replace(99)
    assert ev_type1 == "T1->B1"
    assert ev_page1 == 10

    # Scenario B: len(T1) = 1, len(T2) = 3, p = 1.6667, page in B2
    # If rounded down (int(1.6667) == 1), len(T1) == 1 would falsely trigger equality!
    # Without rounding: 1 == 1.6667 is False, 1 > 1.6667 is False -> correctly evicts T2!
    arc2 = ARCCache(4)
    arc2.p = 5.0 / 3.0
    arc2.T1[10] = None                     # len(T1) = 1
    arc2.T2[20] = None; arc2.T2[21] = None; arc2.T2[22] = None  # len(T2) = 3
    arc2.B2[99] = None
    ev_type2, ev_page2 = arc2._replace(99)
    assert ev_type2 == "T2->B2"
    assert ev_page2 == 20


def test_b1_adaptation_non_integer_ratio_float():
    """
    Verify paper-faithful float adaptation on B1 hit with non-integer ratio:
    |B1| = 3, |B2| = 5 -> delta1 = 5 / 3 = 1.666666...
    Contrasts with integer division (5 // 3 = 1).
    """
    arc = ARCCache(10)  # Default: use_integer_p=False
    arc.p = 0.0
    for x in range(1, 4):      # |B1| = 3
        arc.B1[x] = None
    for x in range(10, 15):    # |B2| = 5
        arc.B2[x] = None
    for x in range(20, 26):    # |T1| = 6
        arc.T1[x] = None
    for x in range(30, 34):    # |T2| = 4 -> |T1| + |T2| = 10 == c
        arc.T2[x] = None

    res = arc.request(1)       # Hit in B1
    assert res["case"] == "CASE_2_B1_HIT"
    assert res["delta"] == pytest.approx(5.0 / 3.0)
    assert arc.p == pytest.approx(5.0 / 3.0)
    assert arc.p != 1          # Confirms float ratio was preserved


def test_b2_adaptation_non_integer_ratio_float():
    """
    Verify paper-faithful float adaptation on B2 hit with non-integer ratio:
    |B1| = 5, |B2| = 3 -> delta2 = 5 / 3 = 1.666666...
    """
    arc = ARCCache(10)  # Default: use_integer_p=False
    arc.p = 4.0
    for x in range(1, 6):      # |B1| = 5
        arc.B1[x] = None
    for x in range(10, 13):    # |B2| = 3
        arc.B2[x] = None
    for x in range(20, 25):    # |T1| = 5
        arc.T1[x] = None
    for x in range(30, 35):    # |T2| = 5 -> |T1| + |T2| = 10 == c
        arc.T2[x] = None

    res = arc.request(10)      # Hit in B2
    assert res["case"] == "CASE_3_B2_HIT"
    assert res["delta"] == pytest.approx(5.0 / 3.0)
    assert arc.p == pytest.approx(4.0 - 5.0 / 3.0)


def test_integer_variant_adaptation():
    """
    Verify the optional integer variant (use_integer_p=True) uses integer floor division:
    |B1| = 3, |B2| = 5 -> delta1 = 5 // 3 = 1.
    """
    arc = ARCCache(10, use_integer_p=True)
    arc.p = 0
    for x in range(1, 4):      # |B1| = 3
        arc.B1[x] = None
    for x in range(10, 15):    # |B2| = 5
        arc.B2[x] = None
    for x in range(20, 26):    # |T1| = 6
        arc.T1[x] = None
    for x in range(30, 34):    # |T2| = 4 -> |T1| + |T2| = 10 == c
        arc.T2[x] = None

    res = arc.request(1)
    assert res["case"] == "CASE_2_B1_HIT"
    assert res["delta"] == 1   # 5 // 3 = 1
    assert arc.p == 1
    assert isinstance(arc.p, int)


def test_case_4a_when_l1_full_and_t1_less_than_c():
    """
    Case 4A: When |L1| == c and |T1| < c, ARC deletes the LRU page in B1,
    calls REPLACE(p, x_t), and fetches x_t into MRU of T1.
    """
    arc = ARCCache(3)
    arc.p = 1.0
    arc.T1[1] = None      # |T1| = 1
    arc.B1[2] = None      # LRU of B1
    arc.B1[3] = None      # MRU of B1 -> |B1| = 2, |L1| = 1 + 2 = 3 == c
    arc.T2[4] = None      # LRU of T2
    arc.T2[5] = None      # MRU of T2 -> |T2| = 2, cache size = 1 + 2 = 3 == c

    res = arc.request(99)  # New miss not in L1 U L2
    assert res["case"] == "CASE_4A_L1_FULL"
    assert res["ghost_evicted"] == 2  # LRU of B1 was purged
    assert res["eviction_type"] == "T2->B2"
    assert res["evicted_page"] == 4   # REPLACE evicted LRU of T2
    st = arc.get_state()
    assert st["T1"] == [99, 1]
    assert st["B1"] == [3]
    assert st["T2"] == [5]
    assert st["B2"] == [4]


def test_case_4b_total_directory_2c_purges_b2():
    """
    Case 4B: When |L1| < c and total directory |L1| + |L2| == 2c,
    ARC deletes the LRU page in B2, calls REPLACE(p, x_t), and fetches x_t into T1.
    """
    arc = ARCCache(2)
    arc.p = 0.0
    arc.T1[10] = None     # |T1| = 1, |B1| = 0 -> |L1| = 1 < c
    arc.T2[20] = None     # |T2| = 1 -> |T1| + |T2| = 2 == c
    arc.B2[30] = None     # LRU of B2
    arc.B2[40] = None     # MRU of B2 -> |B2| = 2, |L2| = 3 -> total = 4 == 2c

    res = arc.request(99)  # New miss
    assert res["case"] == "CASE_4B_L1_LESS"
    assert res["ghost_evicted"] == 30  # LRU of B2 purged
    assert res["eviction_type"] == "T1->B1"
    assert res["evicted_page"] == 10   # |T1| > p (1 > 0.0) -> evicts T1
    st = arc.get_state()
    assert st["T1"] == [99]
    assert st["T2"] == [20]
    assert st["B1"] == [10]
    assert st["B2"] == [40]


def test_capacity_one():
    """Verify ARC operation with minimal capacity c = 1."""
    arc = ARCCache(1)
    res1 = arc.request("A")
    assert res1["hit"] is False
    assert arc.get_state()["T1"] == ["A"]

    res2 = arc.request("B")
    assert res2["hit"] is False
    assert res2["evicted_page"] == "A"
    assert arc.get_state()["T1"] == ["B"]

    res3 = arc.request("B")
    assert res3["hit"] is True
    assert arc.get_state()["T2"] == ["B"]
    assert arc.get_state()["T1"] == []


def test_repeated_t2_hits():
    """Verify multiple consecutive hits on a page in T2 preserve MRU position and size."""
    arc = ARCCache(3)
    arc.request(1)
    arc.request(2)
    arc.request(2)  # 2 promoted to T2
    assert arc.get_state()["T2"] == [2]

    # Repeated hits on page 2
    for _ in range(5):
        res = arc.request(2)
        assert res["hit"] is True
        assert res["hit_type"] == "T2_HIT"
        assert arc.get_state()["T2"] == [2]
    assert arc.hits == 6


def test_reset_functionality():
    """Verify reset restores pristine initial state."""
    arc = ARCCache(3)
    arc.request(1)
    arc.request(2)
    arc.request(3)
    arc.request(1)
    assert arc.requests > 0
    assert arc.hits > 0

    arc.reset()
    assert arc.p == 0.0
    assert arc.requests == 0
    assert arc.hits == 0
    assert arc.misses == 0
    assert arc.replacements == 0
    st = arc.get_state()
    assert st["T1"] == []
    assert st["T2"] == []
    assert st["B1"] == []
    assert st["B2"] == []


def test_boundary_clamping_p_float():
    """Verify default float p is strictly clamped to [0.0, float(c)]."""
    arc = ARCCache(4)
    # Clamp at c
    arc.p = 2.5
    arc.B1[1] = None            # |B1| = 1
    for x in range(2, 5):       # |B2| = 3 -> delta = 3.0 / 1.0 = 3.0
        arc.B2[x] = None
    for x in range(10, 14):     # |T2| = 4 -> total directory = 8 == 2c
        arc.T2[x] = None

    # Hit in B1: 2.5 + 3.0 = 5.5 -> clamped to 4.0
    res = arc.request(1)
    assert res["case"] == "CASE_2_B1_HIT"
    assert res["delta"] == 3.0
    assert arc.p == 4.0

    # Clamp at 0.0
    arc.reset()
    arc.p = 1.5
    for x in range(1, 4):       # |B1| = 3
        arc.B1[x] = None
    arc.B2[4] = None            # |B2| = 1 -> delta = 3.0 / 1.0 = 3.0
    arc.T1[10] = None           # |T1| = 1
    for x in range(20, 23):     # |T2| = 3 -> cache full = 4 == c
        arc.T2[x] = None

    # Hit in B2: 1.5 - 3.0 = -1.5 -> clamped to 0.0
    res = arc.request(4)
    assert res["case"] == "CASE_3_B2_HIT"
    assert res["delta"] == 3.0
    assert arc.p == 0.0


def test_boundary_clamping_p_integer_variant():
    """Verify optional integer variant clamps p strictly to [0, c]."""
    arc = ARCCache(4, use_integer_p=True)
    arc.p = 2
    arc.B1[1] = None
    for x in range(2, 5):
        arc.B2[x] = None
    for x in range(10, 14):
        arc.T2[x] = None

    res = arc.request(1)
    assert res["delta"] == 3
    assert arc.p == 4

    arc.reset()
    arc.p = 1
    for x in range(1, 4):
        arc.B1[x] = None
    arc.B2[4] = None
    arc.T1[10] = None
    for x in range(20, 23):
        arc.T2[x] = None

    res = arc.request(4)
    assert res["delta"] == 3
    assert arc.p == 0


def test_random_stress_invariants():
    """Stress test with 1,000 random page references to verify all paper invariants hold continuously."""
    capacity = 10
    arc = ARCCache(capacity)  # Default float
    pages = list(range(1, 50))

    random.seed(42)
    for _ in range(1000):
        page = random.choice(pages)
        res = arc.request(page)
        state = res["state"]
        
        # Verify paper invariants at every step
        assert state["size_cache"] <= capacity
        assert state["size_total"] <= 2 * capacity
        assert state["size_T1"] + state["size_B1"] <= capacity
        assert 0.0 <= state["p"] <= float(capacity)


def test_invariant_verification_across_edge_cases():
    """Verify _verify_invariants continuously validates all 5 ARC invariants across edge capacities."""
    for cap in [1, 2, 4, 8]:
        arc = ARCCache(cap)
        # Empty cache validation
        arc._verify_invariants()

        # Fill and verify
        for p in range(1, cap * 2):
            arc.request(p)
            arc._verify_invariants()
            assert 0.0 <= arc.p <= cap
            assert len(arc.T1) + len(arc.T2) <= cap
            assert len(arc.T1) + len(arc.B1) + len(arc.T2) + len(arc.B2) <= 2 * cap
