"""
Unit and invariant test suite for Least Recently Used (LRU) Cache.
Verifies classic recency replacement behavior and counters.
"""
import pytest
import random
from backend.algorithms.lru import LRUCache


def test_capacity_validation():
    """Verify capacity validation enforces positive integers."""
    with pytest.raises(ValueError):
        LRUCache(0)
    with pytest.raises(ValueError):
        LRUCache(-1)
    with pytest.raises(ValueError):
        LRUCache("4")  # type: ignore
    with pytest.raises(ValueError):
        LRUCache(2.5)  # type: ignore

    lru = LRUCache(3)
    assert lru.capacity == 3


def test_initial_empty_cache():
    """Verify initial cache state is empty."""
    lru = LRUCache(3)
    st = lru.get_state()
    assert st["capacity"] == 3
    assert st["size"] == 0
    assert st["pages"] == []
    assert st["mru"] is None
    assert st["lru"] is None
    assert st["requests"] == 0
    assert st["hits"] == 0
    assert st["misses"] == 0
    assert st["replacements"] == 0
    assert st["hit_ratio"] == 0.0


def test_first_page_request_and_insertion():
    """Verify first request causes a miss and inserts page at MRU."""
    lru = LRUCache(3)
    res = lru.request("A")
    assert res["page"] == "A"
    assert res["hit"] is False
    assert res["hit_type"] == "MISS"
    assert res["evicted_page"] is None
    st = res["state"]
    assert st["pages"] == ["A"]
    assert st["mru"] == "A"
    assert st["lru"] == "A"
    assert lru.requests == 1
    assert lru.misses == 1
    assert lru.hits == 0


def test_page_fault_when_cache_not_full():
    """Verify multiple cold misses insert pages in MRU order without evictions."""
    lru = LRUCache(3)
    lru.request(1)
    lru.request(2)
    res3 = lru.request(3)

    assert res3["hit"] is False
    assert res3["evicted_page"] is None
    st = lru.get_state()
    assert st["size"] == 3
    assert st["mru_to_lru"] == [3, 2, 1]  # 3 is MRU, 1 is LRU
    assert st["mru"] == 3
    assert st["lru"] == 1
    assert lru.replacements == 0


def test_page_hit_and_mru_promotion():
    """Verify page hit promotes the referenced page to MRU."""
    lru = LRUCache(3)
    lru.request("A")
    lru.request("B")
    lru.request("C")
    # State: MRU [C, B, A] LRU

    # Access B (hit)
    res = lru.request("B")
    assert res["hit"] is True
    assert res["hit_type"] == "HIT"
    assert res["evicted_page"] is None
    st = lru.get_state()
    assert st["mru_to_lru"] == ["B", "C", "A"]  # B promoted to MRU, A is still LRU
    assert st["mru"] == "B"
    assert st["lru"] == "A"
    assert lru.hits == 1
    assert lru.misses == 3
    assert lru.hit_ratio == 0.25


def test_eviction_of_correct_lru_page():
    """Verify when cache is full, the least recently used page is evicted."""
    lru = LRUCache(3)
    lru.request(1)
    lru.request(2)
    lru.request(3)
    # Order: MRU [3, 2, 1] LRU

    # Access 2 to refresh its recency
    lru.request(2)
    # Order: MRU [2, 3, 1] LRU -> 1 is LRU

    # Request 4 (cache full) -> should evict 1
    res = lru.request(4)
    assert res["hit"] is False
    assert res["evicted_page"] == 1
    st = lru.get_state()
    assert st["mru_to_lru"] == [4, 2, 3]  # 4 is MRU, 3 is now LRU
    assert st["mru"] == 4
    assert st["lru"] == 3
    assert lru.replacements == 1

    # Request 5 (cache full) -> should evict 3
    res2 = lru.request(5)
    assert res2["hit"] is False
    assert res2["evicted_page"] == 3
    st2 = lru.get_state()
    assert st2["mru_to_lru"] == [5, 4, 2]
    assert lru.replacements == 2


def test_repeated_page_hits():
    """Verify repeated hits on the same page keep it at MRU without duplicating."""
    lru = LRUCache(3)
    lru.request("X")
    lru.request("Y")

    for _ in range(5):
        res = lru.request("X")
        assert res["hit"] is True
        st = lru.get_state()
        assert st["size"] == 2
        assert st["mru"] == "X"
        assert st["lru"] == "Y"
        assert st["mru_to_lru"] == ["X", "Y"]

    assert lru.hits == 5
    assert lru.misses == 2
    assert lru.requests == 7


def test_capacity_one():
    """Verify LRU operation with minimal capacity c = 1."""
    lru = LRUCache(1)
    # First access
    res1 = lru.request("A")
    assert res1["hit"] is False
    assert res1["evicted_page"] is None
    assert lru.get_state()["pages"] == ["A"]

    # Second access (evicts A)
    res2 = lru.request("B")
    assert res2["hit"] is False
    assert res2["evicted_page"] == "A"
    assert lru.get_state()["pages"] == ["B"]

    # Hit on B
    res3 = lru.request("B")
    assert res3["hit"] is True
    assert res3["evicted_page"] == None
    assert lru.get_state()["pages"] == ["B"]


def test_reset_functionality():
    """Verify reset restores an empty cache and clears all counters."""
    lru = LRUCache(3)
    for x in [1, 2, 3, 2, 4]:
        lru.request(x)

    assert lru.requests == 5
    assert lru.hits == 1
    assert lru.misses == 4
    assert lru.replacements == 1

    lru.reset()
    st = lru.get_state()
    assert st["size"] == 0
    assert st["pages"] == []
    assert st["requests"] == 0
    assert st["hits"] == 0
    assert st["misses"] == 0
    assert st["replacements"] == 0
    assert st["hit_ratio"] == 0.0


def test_counter_accounting():
    """Verify accurate calculation of requests, hits, misses, and hit ratio."""
    lru = LRUCache(2)
    # 4 misses, 2 hits -> 6 requests -> hit ratio = 2/6 = 0.3333
    trace = [1, 2, 1, 3, 2, 3]
    # req 1: miss (1)
    # req 2: miss (2) -> [2, 1]
    # req 1: hit (1) -> [1, 2]
    # req 3: miss (evict 2) -> [3, 1]
    # req 2: miss (evict 1) -> [2, 3]
    # req 3: hit (3) -> [3, 2]
    for page in trace:
        lru.request(page)

    assert lru.requests == 6
    assert lru.hits == 2
    assert lru.misses == 4
    assert lru.replacements == 2
    assert lru.hit_ratio == round(2 / 6, 4)


def test_random_stress_and_invariants():
    """Stress test with 1,000 random page references to verify capacity invariant."""
    capacity = 8
    lru = LRUCache(capacity)
    pages = list(range(1, 30))

    random.seed(123)
    for _ in range(1000):
        page = random.choice(pages)
        res = lru.request(page)
        st = res["state"]
        
        # Invariants
        assert st["size"] <= capacity
        assert len(st["pages"]) <= capacity
        assert len(set(st["pages"])) == st["size"]  # Uniqueness
        assert lru.requests == lru.hits + lru.misses

