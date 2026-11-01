# ARC Page Replacement Simulator

An Operating Systems project implementing and benchmarking the **Adaptive Replacement Cache (ARC)** algorithm against **Least Recently Used (LRU)** and **First-In, First-Out (FIFO)**, with step-by-step state visualization, synthetic workload generation, and comparative analytics.

Based on the landmark research paper:
> **"ARC: A Self-Tuning, Low Overhead Replacement Cache"**  
> Nimrod Megiddo and Dharmendra S. Modha  
> *USENIX Conference on File and Storage Technologies (FAST 2003)*

---

## Features

- **Paper-Faithful ARC Algorithm**:
  - Implements the complete four-list directory structure: $T_1$ (recency resident), $T_2$ (frequency resident), $B_1$ (recency ghost history), and $B_2$ (frequency ghost history).
  - Maintains the online adaptive target parameter $p \in [0, c]$.
  - Exact `REPLACE` eviction procedure including the special condition for $x_t \in B_2$ and $|T_1| == p$.
  - Continuous validation of all 5 ARC mathematical invariants ($|T_1| + |T_2| \le c$, $|L_1| + |L_2| \le 2c$, $|L_1| \le c$, $0 \le p \le c$, and list disjointness).
  - Dual adaptation modes: paper-faithful float ratios $\delta_1 = \max(1.0, |B_2| / |B_1|)$ by default, with optional integer floor division for storage kernel emulation.
- **Independent Baseline Algorithms**:
  - **LRU Cache**: $O(1)$ recency tracking with doubly-linked order, evicting the least recently referenced page on fault.
  - **FIFO Cache**: $O(1)$ arrival queue where hits do not alter insertion order.
- **Simulation Engine & Telemetry**:
  - Reusable execution engine capturing per-step state snapshots, hit/miss classifications, ghost hits, eviction tracking, and cumulative ratios.
  - Snapshot immutability: deep-copied states ensure historical traces remain uncorrupted.
- **Workload Generator**:
  - Synthetic workload generation for custom strings, sequential scans, uniform random sequences, skewed 80/20 Zipfian locality, and cyclic loops (demonstrating Belady's anomaly and scan thrashing).
- **Comparative Analysis Layer**:
  - Multi-algorithm benchmarking running ARC, LRU, and FIFO over identical reference streams.
  - Strict page-fault-only winner selection and tie handling (hits, replacements, and execution times are never used as tie-breakers).
- **FastAPI REST API**:
  - JSON API endpoints for service health, single-algorithm step simulation, multi-algorithm comparison, and workload generation.
  - Pydantic v2 validation models and clean HTTP error responses.
- **Interactive Dark-Theme Dashboard**:
  - Visual memory lists with LRU/MRU orientation and color-coded status pills.
  - Interactive adaptive parameter $p$ split bar.
  - Step-by-step playback controls (Play, Pause, Step Next, Step Prev, Reset, Speed Slider).
  - Real-time Chart.js bar charts for page faults, hits, and hit ratio percentages.

---

## Technology Stack

- **Backend**: Python 3.10+, FastAPI, Uvicorn, Pydantic v2, Starlette
- **Testing**: pytest (90 automated tests)
- **Frontend**: HTML5, CSS3 (Modern Dark Design System), Vanilla JavaScript (ES6+)
- **Charting**: Chart.js 4.4+

---

## Project Structure

```text
ARC-Page-Replacement/
├── backend/
│   ├── algorithms/          # Independent replacement algorithms (no framework dependencies)
│   │   ├── __init__.py      # Exports ARCCache, LRUCache, FIFOCache
│   │   ├── arc.py           # Adaptive Replacement Cache implementation
│   │   ├── lru.py           # Least Recently Used cache implementation
│   │   └── fifo.py          # First-In, First-Out cache implementation
│   ├── simulation/          # Common simulation engine and workload generators
│   │   ├── __init__.py      # Exports SimulationEngine, workload generators
│   │   ├── engine.py        # Step-by-step simulation executor and metrics collector
│   │   └── workload.py      # Synthetic workload generation strategies
│   ├── analysis/            # Comparative analysis and benchmark layer
│   │   ├── __init__.py      # Exports CacheComparator, compare_algorithms
│   │   └── comparator.py    # Multi-algorithm comparison and page-fault tie logic
│   ├── tests/               # Unit, regression, and API integration test suite
│   │   ├── test_arc.py          # ARC algorithm and invariant tests
│   │   ├── test_lru.py          # LRU algorithm tests
│   │   ├── test_fifo.py         # FIFO algorithm tests
│   │   ├── test_simulation.py   # Simulation engine and workload tests
│   │   ├── test_comparator.py   # Comparative benchmarking and tie tests
│   │   ├── test_api.py          # FastAPI endpoint integration tests
│   │   └── test_environment.py # Environment and package sanity tests
│   ├── main.py              # FastAPI server, route endpoints, and static file mount
│   └── __init__.py
├── frontend/
│   ├── index.html           # Single-page dashboard interface
│   ├── css/
│   │   └── style.css        # Responsive dark-theme design system
│   └── js/
│       └── app.js           # Frontend controller, API fetcher, and Chart.js integration
├── requirements.txt         # Pinned Python package dependencies
├── .gitignore               # Ignored files (virtual environment, cache, logs)
└── README.md                # Project documentation and user guide
```

---

## Installation & Setup

### 1. Clone or Open the Repository
```powershell
cd E:\OS_PROJECT
```

### 2. Activate the Virtual Environment
```powershell
# Windows PowerShell
.\venv\Scripts\Activate.ps1

# Or Windows Command Prompt
.\venv\Scripts\activate.bat
```

*(If creating a new virtual environment from scratch: `python -m venv venv` followed by `pip install -r requirements.txt`)*

---

## Running the Application

### 1. Start the FastAPI Server
```powershell
.\venv\Scripts\python.exe -m uvicorn backend.main:app --reload --port 8000
```

### 2. Open the Dashboard in Your Browser
Navigate to:
👉 **[http://127.0.0.1:8000/](http://127.0.0.1:8000/)** (or [http://localhost:8000/](http://localhost:8000/))

---

## Running the Automated Test Suite

Run the full pytest suite (90 passing tests across algorithms, simulation, comparator, and API):

```powershell
.\venv\Scripts\python.exe -m pytest -v backend/tests/
```

To run individual test modules:
```powershell
# Algorithm tests
.\venv\Scripts\python.exe -m pytest -v backend/tests/test_arc.py
.\venv\Scripts\python.exe -m pytest -v backend/tests/test_lru.py
.\venv\Scripts\python.exe -m pytest -v backend/tests/test_fifo.py

# Simulation & Workload tests
.\venv\Scripts\python.exe -m pytest -v backend/tests/test_simulation.py

# Comparative analysis & tie tests
.\venv\Scripts\python.exe -m pytest -v backend/tests/test_comparator.py

# FastAPI integration tests
.\venv\Scripts\python.exe -m pytest -v backend/tests/test_api.py
```

---

## API Endpoints Reference

| Method | Endpoint | Description | Request Payload | Response Summary |
| :--- | :--- | :--- | :--- | :--- |
| `GET` | `/api/health` | Health check and version | None | `{"status": "healthy", "service": "...", "version": "1.0.0"}` |
| `POST` | `/api/simulate` | Execute step-by-step cache simulation | `{"algorithm": "ARC", "capacity": 4, "reference_string": [1,2,3,1]}` | `{"algorithm": "...", "metrics": {...}, "steps": [...], "final_state": {...}}` |
| `POST` | `/api/compare` | Benchmark ARC, LRU, and FIFO simultaneously | `{"capacity": 4, "reference_string": [1,2,3,1,4], "include_steps": false}` | `{"results": {...}, "best_algorithm": "...", "is_tie": bool, "comparison_summary": {...}}` |
| `POST` | `/api/workload` | Generate synthetic reference strings | `{"workload_type": "repeated_locality", "length": 20, "num_pages": 8}` | `{"workload_type": "...", "length": 20, "reference_string": [...]}` |

---

## Theory & ARC Architecture

ARC splits cache capacity $c$ between two lists:
- **$L_1 = T_1 \cup B_1$** (Recency): Tracks pages requested once recently.
- **$L_2 = T_2 \cup B_2$** (Frequency): Tracks pages requested $\ge 2$ times recently.

### The Five ARC Invariants
1. $|T_1| + |T_2| \le c$ (Resident cache capacity bound)
2. $|T_1| + |T_2| + |B_1| + |B_2| \le 2c$ (Total directory capacity bound)
3. $|T_1| + |B_1| \le c$ ($L_1$ directory bound)
4. $0 \le p \le c$ (Target parameter bound)
5. $T_1, T_2, B_1, B_2$ are mutually disjoint.

### Online Learning Equations
When a page fault occurs on page $x$:
- **If $x \in B_1$ (Ghost hit in recency history)**:
  $$p \leftarrow \min\left(c, p + \max\left(1.0, \frac{|B_2|}{|B_1|}\right)\right)$$
- **If $x \in B_2$ (Ghost hit in frequency history)**:
  $$p \leftarrow \max\left(0.0, p - \max\left(1.0, \frac{|B_1|}{|B_2|}\right)\right)$$

### Eviction Procedure `REPLACE(p, x)`
- If $|T_1| \ge 1$ and $( (x \in B_2 \text{ and } |T_1| == p) \text{ or } (|T_1| > p) )$:
  Evict the LRU page of $T_1$ to the top (MRU) of $B_1$.
- Otherwise:
  Evict the LRU page of $T_2$ to the top (MRU) of $B_2$.
