"""
API Integration Tests for ARC Page Replacement Simulator.
Tests all endpoints using FastAPI TestClient:
  - GET /api/health
  - POST /api/simulate (ARC, LRU, FIFO, invalid algorithms, validation errors)
  - POST /api/compare (winner selection, tie handling, error validation)
  - POST /api/workload (all workload strategies, parameter validation)
  - Response JSON serializability and schema integrity
"""
import json
import pytest
from fastapi.testclient import TestClient
from backend.main import app

client = TestClient(app)


# ---------------------------------------------------------
# 1. Health Endpoint Tests
# ---------------------------------------------------------

def test_health_endpoint():
    """Verify health endpoint returns healthy status and metadata."""
    response = client.get("/api/health")
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "healthy"
    assert "ARC Page Replacement Simulator" in data["service"]
    assert data["version"] == "1.0.0"


def test_static_frontend_mount():
    """Verify static frontend files are served at root GET /."""
    response = client.get("/")
    assert response.status_code == 200
    assert "text/html" in response.headers.get("content-type", "")


# ---------------------------------------------------------
# 2. Simulation Endpoint Tests
# ---------------------------------------------------------

def test_simulate_arc():
    """Verify ARC simulation execution through /api/simulate."""
    payload = {
        "algorithm": "ARC",
        "capacity": 3,
        "reference_string": [1, 2, 3, 1, 2, 4, 1, 2]
    }
    response = client.post("/api/simulate", json=payload)
    assert response.status_code == 200
    data = response.json()

    assert data["algorithm"] == "ARC"
    assert data["capacity"] == 3
    assert data["reference_string"] == [1, 2, 3, 1, 2, 4, 1, 2]

    metrics = data["metrics"]
    assert metrics["total_requests"] == 8
    assert metrics["total_hits"] + metrics["total_misses"] == 8
    assert "execution_time_ms" in metrics
    assert "hit_ratio" in metrics

    steps = data["steps"]
    assert len(steps) == 8
    assert steps[0]["step"] == 1
    assert steps[0]["page"] == 1
    assert "p" in steps[0]
    assert "T1" in steps[0]

    final_state = data["final_state"]
    assert "p" in final_state
    assert "T1" in final_state
    assert "T2" in final_state


def test_simulate_lru():
    """Verify LRU simulation execution through /api/simulate."""
    payload = {
        "algorithm": "LRU",
        "capacity": 3,
        "reference_string": [1, 2, 3, 1, 4, 1]
    }
    response = client.post("/api/simulate", json=payload)
    assert response.status_code == 200
    data = response.json()

    assert data["algorithm"] == "LRU"
    assert data["capacity"] == 3
    metrics = data["metrics"]
    assert metrics["total_requests"] == 6
    # 1, 2, 3 (miss), 1 (hit), 4 (miss, evicts 2), 1 (hit) -> 4 misses, 2 hits
    assert metrics["total_misses"] == 4
    assert metrics["total_hits"] == 2

    assert len(data["steps"]) == 6
    assert "pages" in data["final_state"]


def test_simulate_fifo():
    """Verify FIFO simulation execution through /api/simulate."""
    payload = {
        "algorithm": "FIFO",
        "capacity": 3,
        "reference_string": [1, 2, 3, 1, 4, 1]
    }
    response = client.post("/api/simulate", json=payload)
    assert response.status_code == 200
    data = response.json()

    assert data["algorithm"] == "FIFO"
    assert data["capacity"] == 3
    metrics = data["metrics"]
    assert metrics["total_requests"] == 6
    # 1, 2, 3 (miss), 1 (hit), 4 (miss, evicts 1), 1 (miss) -> 5 misses, 1 hit
    assert metrics["total_misses"] == 5
    assert metrics["total_hits"] == 1

    assert len(data["steps"]) == 6
    assert "pages" in data["final_state"]


def test_simulate_case_insensitivity():
    """Verify algorithm name accepts lowercase/mixed case (e.g. 'arc', 'Lru', 'FiFo')."""
    for algo_input in ["arc", "Lru", "FiFo"]:
        response = client.post("/api/simulate", json={
            "algorithm": algo_input,
            "capacity": 2,
            "reference_string": [1, 2, 1]
        })
        assert response.status_code == 200
        assert response.json()["algorithm"] == algo_input.upper()


# ---------------------------------------------------------
# 3. Comparison Endpoint Tests
# ---------------------------------------------------------

def test_compare_endpoint_structure():
    """Verify /api/compare executes ARC, LRU, and FIFO and returns comparative metrics."""
    payload = {
        "capacity": 3,
        "reference_string": [1, 2, 3, 1, 4, 1, 5, 2],
        "include_steps": True
    }
    response = client.post("/api/compare", json=payload)
    assert response.status_code == 200
    data = response.json()

    assert data["capacity"] == 3
    assert data["reference_string"] == [1, 2, 3, 1, 4, 1, 5, 2]
    assert "results" in data
    assert set(data["results"].keys()) == {"ARC", "LRU", "FIFO"}

    # Verify include_steps includes step history for each algorithm
    assert "steps" in data["results"]["ARC"]
    assert "steps" in data["results"]["LRU"]
    assert "steps" in data["results"]["FIFO"]

    assert "comparison_summary" in data
    assert "ranking" in data["comparison_summary"]


def test_compare_page_fault_single_winner():
    """
    Verify /api/compare correctly identifies a single winner based strictly on lowest page faults.
    Trace [1, 2, 3, 1, 2, 3, 4, 5, 6, 2, 3] with c=3 gives ARC 6 faults, LRU 8, FIFO 8.
    """
    payload = {
        "capacity": 3,
        "reference_string": [1, 2, 3, 1, 2, 3, 4, 5, 6, 2, 3],
        "include_steps": False
    }
    response = client.post("/api/compare", json=payload)
    assert response.status_code == 200
    data = response.json()

    assert data["is_tie"] is False
    assert data["best_algorithm"] == "ARC"
    assert data["best_algorithms"] == ["ARC"]

    # Verify ranking structure
    ranking = data["comparison_summary"]["ranking"]
    assert ranking[0]["rank"] == 1
    assert ranking[0]["algorithms"] == ["ARC"]
    assert ranking[0]["page_faults"] == 6
    assert ranking[0]["is_tie"] is False


def test_compare_page_fault_tie_behavior():
    """
    Verify /api/compare handles ties properly when multiple algorithms share minimum page faults:
      - is_tie = True
      - best_algorithm = None
      - best_algorithms contains all tied algorithms
    """
    payload = {
        "capacity": 2,
        "reference_string": [1, 2, 1, 2]
    }
    response = client.post("/api/compare", json=payload)
    assert response.status_code == 200
    data = response.json()

    assert data["is_tie"] is True
    assert data["best_algorithm"] is None
    assert set(data["best_algorithms"]) == {"ARC", "LRU", "FIFO"}

    ranking = data["comparison_summary"]["ranking"]
    assert len(ranking) == 1
    assert ranking[0]["rank"] == 1
    assert set(ranking[0]["algorithms"]) == {"ARC", "LRU", "FIFO"}
    assert ranking[0]["is_tie"] is True


# ---------------------------------------------------------
# 4. Workload Endpoint Tests
# ---------------------------------------------------------

def test_workload_sequential():
    """Verify sequential workload generation through /api/workload."""
    payload = {
        "workload_type": "sequential",
        "length": 8,
        "num_pages": 4,
        "start_page": 1
    }
    response = client.post("/api/workload", json=payload)
    assert response.status_code == 200
    data = response.json()
    assert data["workload_type"] == "sequential"
    assert data["length"] == 8
    assert data["reference_string"] == [1, 2, 3, 4, 1, 2, 3, 4]


def test_workload_random():
    """Verify random workload generation with deterministic seed."""
    payload = {
        "workload_type": "random",
        "length": 10,
        "num_pages": 5,
        "start_page": 1,
        "seed": 42
    }
    response1 = client.post("/api/workload", json=payload)
    response2 = client.post("/api/workload", json=payload)
    assert response1.status_code == 200
    assert response2.status_code == 200

    data1 = response1.json()
    data2 = response2.json()
    assert len(data1["reference_string"]) == 10
    # Deterministic reproducibility via seed
    assert data1["reference_string"] == data2["reference_string"]
    assert all(1 <= p <= 5 for p in data1["reference_string"])


def test_workload_repeated_locality():
    """Verify repeated_locality (Zipfian) workload generation."""
    payload = {
        "workload_type": "repeated_locality",
        "length": 50,
        "num_pages": 10,
        "hot_pages_count": 2,
        "hot_prob": 0.9,
        "seed": 123
    }
    response = client.post("/api/workload", json=payload)
    assert response.status_code == 200
    data = response.json()
    assert data["length"] == 50
    # Hot pages are 1 and 2
    hot_count = sum(1 for p in data["reference_string"] if p in [1, 2])
    # Expect ~90% hot pages
    assert hot_count >= 35


def test_workload_looping():
    """Verify looping workload generation."""
    payload = {
        "workload_type": "looping",
        "length": 9,
        "loop_size": 3,
        "start_page": 1
    }
    response = client.post("/api/workload", json=payload)
    assert response.status_code == 200
    data = response.json()
    assert data["reference_string"] == [1, 2, 3, 1, 2, 3, 1, 2, 3]


def test_workload_custom():
    """Verify custom workload generation with repeat/truncation."""
    payload = {
        "workload_type": "custom",
        "length": 6,
        "pages": ["A", "B"]
    }
    response = client.post("/api/workload", json=payload)
    assert response.status_code == 200
    data = response.json()
    assert data["reference_string"] == ["A", "B", "A", "B", "A", "B"]


# ---------------------------------------------------------
# 5. Validation and Error Handling Tests
# ---------------------------------------------------------

def test_invalid_algorithm():
    """Verify meaningful HTTP error when an unknown algorithm is requested."""
    payload = {
        "algorithm": "UNKNOWN_ALGO",
        "capacity": 3,
        "reference_string": [1, 2, 3]
    }
    response = client.post("/api/simulate", json=payload)
    assert response.status_code in [400, 422]
    detail = str(response.json()["detail"])
    assert "Unsupported algorithm" in detail or "value_error" in detail


def test_invalid_capacity():
    """Verify meaningful HTTP error for non-positive or invalid capacity."""
    # Zero capacity
    res1 = client.post("/api/simulate", json={"algorithm": "ARC", "capacity": 0, "reference_string": [1]})
    assert res1.status_code in [400, 422]

    # Negative capacity
    res2 = client.post("/api/simulate", json={"algorithm": "ARC", "capacity": -2, "reference_string": [1]})
    assert res2.status_code in [400, 422]

    # Non-integer capacity in compare
    res3 = client.post("/api/compare", json={"capacity": "invalid", "reference_string": [1]})
    assert res3.status_code in [400, 422]


def test_invalid_reference_string():
    """Verify meaningful HTTP error when reference_string is not a list or missing."""
    res1 = client.post("/api/simulate", json={"algorithm": "ARC", "capacity": 3, "reference_string": "not_a_list"})
    assert res1.status_code in [400, 422]

    res2 = client.post("/api/compare", json={"capacity": 3, "reference_string": None})
    assert res2.status_code in [400, 422]


def test_invalid_workload_parameters():
    """Verify error responses for invalid workload type or parameters."""
    # Unknown workload type
    res1 = client.post("/api/workload", json={"workload_type": "quantum_random", "length": 10})
    assert res1.status_code in [400, 422]

    # Negative length
    res2 = client.post("/api/workload", json={"workload_type": "sequential", "length": -5, "num_pages": 3})
    assert res2.status_code in [400, 422]

    # hot_pages_count > num_pages
    res3 = client.post("/api/workload", json={
        "workload_type": "repeated_locality",
        "length": 10,
        "num_pages": 4,
        "hot_pages_count": 8
    })
    assert res3.status_code in [400, 422]


# ---------------------------------------------------------
# 6. JSON Serialization Test
# ---------------------------------------------------------

def test_json_serializability_all_endpoints():
    """Verify that all API response bodies serialize cleanly to JSON."""
    # 1. Health
    r_health = client.get("/api/health")
    assert json.dumps(r_health.json()) is not None

    # 2. Simulate
    r_sim = client.post("/api/simulate", json={
        "algorithm": "ARC",
        "capacity": 3,
        "reference_string": [1, 2, 3, 1, 4]
    })
    assert json.dumps(r_sim.json()) is not None

    # 3. Compare
    r_comp = client.post("/api/compare", json={
        "capacity": 3,
        "reference_string": [1, 2, 3, 1, 4],
        "include_steps": True
    })
    assert json.dumps(r_comp.json()) is not None

    # 4. Workload
    r_work = client.post("/api/workload", json={
        "workload_type": "sequential",
        "length": 10,
        "num_pages": 5
    })
    assert json.dumps(r_work.json()) is not None
