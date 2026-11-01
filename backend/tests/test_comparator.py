"""
Unit and integration test suite for the comparative analysis layer.
Verifies multi-algorithm benchmarking across ARC, LRU, and FIFO,
fresh instance isolation, tie detection, page-fault-only winner determination,
and JSON serialization.
"""
import json
from unittest.mock import patch
import pytest
from backend.analysis.comparator import CacheComparator, compare_algorithms


def test_comparison_structure_and_metrics():
    """Verify comparator runs all three algorithms and collects required metrics."""
    capacity = 3
    trace = [1, 2, 3, 1, 4, 1, 5, 2]
    res = compare_algorithms(trace, capacity=capacity)

    assert res["capacity"] == capacity
    assert res["reference_string"] == trace
    assert "results" in res
    assert "ARC" in res["results"]
    assert "LRU" in res["results"]
    assert "FIFO" in res["results"]
    assert "best_algorithm" in res
    assert "best_algorithms" in res
    assert "is_tie" in res
    assert "comparison_summary" in res

    # Verify each algorithm result dictionary structure
    for name in ["ARC", "LRU", "FIFO"]:
        algo_res = res["results"][name]
        assert algo_res["algorithm"] == name
        assert algo_res["capacity"] == capacity
        assert algo_res["total_requests"] == len(trace)
        assert "hits" in algo_res
        assert "misses" in algo_res
        assert "page_faults" in algo_res
        assert "replacements" in algo_res
        assert "hit_ratio" in algo_res
        assert "miss_ratio" in algo_res
        assert "page_fault_rate" in algo_res
        assert "execution_time_ms" in algo_res
        assert "final_state" in algo_res
        assert algo_res["hits"] + algo_res["misses"] == len(trace)


def test_single_winner_fewer_faults():
    """
    Scenario 1: Exactly 1 algorithm achieves fewer page faults than the other two.
    Verifies that:
      - is_tie is False
      - best_algorithm is the single winner
      - best_algorithms contains only that winner
      - ranking tier 1 contains only the single winner
    """
    capacity = 3
    # Known trace where ARC achieves 6 faults while LRU and FIFO each incur 8 faults
    trace = [1, 2, 3, 1, 2, 3, 4, 5, 6, 2, 3]
    res = compare_algorithms(trace, capacity=capacity)

    arc_faults = res["results"]["ARC"]["page_faults"]
    lru_faults = res["results"]["LRU"]["page_faults"]
    fifo_faults = res["results"]["FIFO"]["page_faults"]

    assert arc_faults == 6
    assert lru_faults == 8
    assert fifo_faults == 8

    assert res["is_tie"] is False
    assert res["best_algorithm"] == "ARC"
    assert res["best_algorithms"] == ["ARC"]

    ranking = res["comparison_summary"]["ranking"]
    assert len(ranking) == 2

    # Tier 1: Single winner ARC
    assert ranking[0]["rank"] == 1
    assert ranking[0]["algorithms"] == ["ARC"]
    assert ranking[0]["page_faults"] == 6
    assert ranking[0]["is_tie"] is False

    # Tier 2: LRU and FIFO tied with 8 page faults
    assert ranking[1]["rank"] == 2
    assert set(ranking[1]["algorithms"]) == {"LRU", "FIFO"}
    assert ranking[1]["page_faults"] == 8
    assert ranking[1]["is_tie"] is True


def test_two_algorithms_tied_with_different_replacements():
    """
    Scenario 2: 2 algorithms share the minimum page faults but have DIFFERENT replacements.
    Verifies that:
      - Replacements are NEVER used as a tie-breaker.
      - is_tie is True
      - best_algorithm is None
      - best_algorithms contains both tied algorithms
      - The two algorithms share rank 1 together in the ranking.
    """
    mock_results = {
        "ARC": {
            "page_faults": 4,
            "hits": 6,
            "replacements": 3,  # Higher replacements
            "hit_ratio": 0.60,
            "execution_time_ms": 1.2
        },
        "LRU": {
            "page_faults": 4,
            "hits": 6,
            "replacements": 1,  # Lower replacements
            "hit_ratio": 0.60,
            "execution_time_ms": 0.8
        },
        "FIFO": {
            "page_faults": 6,
            "hits": 4,
            "replacements": 3,
            "hit_ratio": 0.40,
            "execution_time_ms": 0.5
        }
    }

    eval_res = CacheComparator.evaluate_comparison(mock_results)

    # Despite LRU having fewer replacements, it MUST NOT be declared sole winner
    assert eval_res["is_tie"] is True
    assert eval_res["best_algorithm"] is None
    assert set(eval_res["best_algorithms"]) == {"ARC", "LRU"}

    ranking = eval_res["comparison_summary"]["ranking"]
    assert len(ranking) == 2

    # Tier 1: ARC and LRU tied together
    assert ranking[0]["rank"] == 1
    assert set(ranking[0]["algorithms"]) == {"ARC", "LRU"}
    assert ranking[0]["page_faults"] == 4
    assert ranking[0]["is_tie"] is True

    # Tier 2: FIFO at rank 3
    assert ranking[1]["rank"] == 3
    assert ranking[1]["algorithms"] == ["FIFO"]
    assert ranking[1]["page_faults"] == 6
    assert ranking[1]["is_tie"] is False


def test_all_three_algorithms_equal_page_faults():
    """
    Scenario 3: All 3 algorithms achieve identical page faults (3-way tie).
    Verifies that:
      - is_tie is True
      - best_algorithm is None
      - best_algorithms contains all three algorithms
      - ranking has a single tier with all three algorithms tied at rank 1.
    """
    capacity = 2
    # Simple trace where ARC, LRU, and FIFO perform identically
    trace = [1, 2, 1, 2]
    res = compare_algorithms(trace, capacity=capacity)

    for name in ["ARC", "LRU", "FIFO"]:
        assert res["results"][name]["page_faults"] == 2
        assert res["results"][name]["hits"] == 2

    assert res["is_tie"] is True
    assert res["best_algorithm"] is None
    assert set(res["best_algorithms"]) == {"ARC", "LRU", "FIFO"}

    ranking = res["comparison_summary"]["ranking"]
    assert len(ranking) == 1
    assert ranking[0]["rank"] == 1
    assert set(ranking[0]["algorithms"]) == {"ARC", "LRU", "FIFO"}
    assert ranking[0]["page_faults"] == 2
    assert ranking[0]["is_tie"] is True


def test_execution_time_difference_does_not_alter_winner():
    """
    Scenario 4: Execution time difference does not alter the winner.
    Verifies that:
      - Faster execution time does NOT break a page-fault tie.
      - Slower execution time does NOT prevent an algorithm with fewer faults from winning.
    """
    # Case A: Two algorithms tied on page faults; one is 10x faster
    tied_results = {
        "ARC": {
            "page_faults": 3,
            "hits": 7,
            "replacements": 1,
            "hit_ratio": 0.70,
            "execution_time_ms": 12.50  # Slow
        },
        "LRU": {
            "page_faults": 3,
            "hits": 7,
            "replacements": 1,
            "hit_ratio": 0.70,
            "execution_time_ms": 0.50  # Very fast
        },
        "FIFO": {
            "page_faults": 5,
            "hits": 5,
            "replacements": 3,
            "hit_ratio": 0.50,
            "execution_time_ms": 0.30
        }
    }

    eval_tied = CacheComparator.evaluate_comparison(tied_results)
    assert eval_tied["is_tie"] is True
    assert eval_tied["best_algorithm"] is None
    assert set(eval_tied["best_algorithms"]) == {"ARC", "LRU"}

    # Case B: ARC has fewer page faults, but is much slower than LRU and FIFO
    single_winner_results = {
        "ARC": {
            "page_faults": 2,
            "hits": 8,
            "replacements": 0,
            "hit_ratio": 0.80,
            "execution_time_ms": 25.0  # Much slower
        },
        "LRU": {
            "page_faults": 4,
            "hits": 6,
            "replacements": 2,
            "hit_ratio": 0.60,
            "execution_time_ms": 1.0
        },
        "FIFO": {
            "page_faults": 5,
            "hits": 5,
            "replacements": 3,
            "hit_ratio": 0.50,
            "execution_time_ms": 0.8
        }
    }

    eval_winner = CacheComparator.evaluate_comparison(single_winner_results)
    assert eval_winner["is_tie"] is False
    assert eval_winner["best_algorithm"] == "ARC"
    assert eval_winner["best_algorithms"] == ["ARC"]


def test_empty_reference_sequence():
    """
    Scenario 5: Empty reference string results in a 3-way tie.
    Verifies that:
      - is_tie is True
      - best_algorithm is None
      - best_algorithms contains ARC, LRU, FIFO
      - All page faults are 0 without division by zero errors
      - ranking contains all 3 algorithms tied at rank 1.
    """
    res = compare_algorithms([], capacity=4)

    assert res["capacity"] == 4
    assert res["reference_string"] == []
    assert res["is_tie"] is True
    assert res["best_algorithm"] is None
    assert set(res["best_algorithms"]) == {"ARC", "LRU", "FIFO"}

    for name in ["ARC", "LRU", "FIFO"]:
        algo_res = res["results"][name]
        assert algo_res["total_requests"] == 0
        assert algo_res["hits"] == 0
        assert algo_res["misses"] == 0
        assert algo_res["page_faults"] == 0
        assert algo_res["hit_ratio"] == 0.0
        assert algo_res["miss_ratio"] == 0.0

    ranking = res["comparison_summary"]["ranking"]
    assert len(ranking) == 1
    assert ranking[0]["rank"] == 1
    assert set(ranking[0]["algorithms"]) == {"ARC", "LRU", "FIFO"}
    assert ranking[0]["page_faults"] == 0
    assert ranking[0]["is_tie"] is True


def test_invalid_capacity_and_inputs():
    """Verify input validation errors for non-positive capacities and None strings."""
    with pytest.raises(ValueError):
        compare_algorithms([1, 2], capacity=0)
    with pytest.raises(ValueError):
        compare_algorithms([1, 2], capacity=-3)
    with pytest.raises(ValueError):
        compare_algorithms([1, 2], capacity="invalid")  # type: ignore
    with pytest.raises(ValueError):
        compare_algorithms(None, capacity=3)  # type: ignore


def test_fresh_algorithm_instances_isolation():
    """Verify comparator creates fresh instances and carries zero state between calls."""
    trace = [1, 2, 3, 1, 2]
    comparator = CacheComparator(capacity=3, reference_string=trace)

    res1 = comparator.compare()
    res2 = comparator.compare()

    # Results of independent runs must be completely identical
    for name in ["ARC", "LRU", "FIFO"]:
        assert res1["results"][name]["hits"] == res2["results"][name]["hits"]
        assert res1["results"][name]["misses"] == res2["results"][name]["misses"]
        assert res1["results"][name]["replacements"] == res2["results"][name]["replacements"]


def test_json_serializability():
    """Verify complete output dictionary can be serialized to JSON without custom encoders."""
    trace = [1, 2, 3, 1, 2, 4, 3, 5]
    res = compare_algorithms(trace, capacity=3, include_steps=True)

    # Must serialize without TypeError
    json_str = json.dumps(res)
    assert json_str is not None
    loaded = json.loads(json_str)
    assert loaded["capacity"] == 3
    assert len(loaded["results"]) == 3
    assert isinstance(loaded["comparison_summary"]["ranking"], list)


def test_known_reference_sequence_behavior():
    """
    Deterministic verification on known reference string:
    [1, 2, 3, 4, 1, 2, 5, 1, 2, 3, 4, 5] with capacity 3.
    """
    capacity = 3
    trace = [1, 2, 3, 4, 1, 2, 5, 1, 2, 3, 4, 5]
    res = compare_algorithms(trace, capacity=capacity)

    assert res["results"]["ARC"]["total_requests"] == 12
    assert res["results"]["LRU"]["total_requests"] == 12
    assert res["results"]["FIFO"]["total_requests"] == 12

    # Verify ranking contains all three algorithms across tiers
    ranking = res["comparison_summary"]["ranking"]
    ranked_algos = [algo for tier in ranking for algo in tier["algorithms"]]
    assert set(ranked_algos) == {"ARC", "LRU", "FIFO"}
    assert len(ranked_algos) == 3
