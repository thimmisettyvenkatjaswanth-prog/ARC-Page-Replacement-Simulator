# ARC Page Replacement Simulator: REST API Documentation

This document specifies the REST API endpoints, JSON request schemas, validation constraints, and response structures for the **ARC Page Replacement Simulator** backend service.

---

## 1. Overview & Service Information

- **Service Title**: ARC Page Replacement Simulator API
- **Framework**: FastAPI (Python 3.10+) with Uvicorn ASGI server
- **Base URL**: `http://localhost:8000` (or `http://127.0.0.1:8000`)
- **Interactive OpenAPI Documentation**: `http://localhost:8000/docs`
- **ReDoc Documentation**: `http://localhost:8000/redoc`
- **Static Frontend UI**: Served at `http://localhost:8000/`

### Global Conventions
- All request payloads must be encoded as `application/json; charset=utf-8`.
- All response payloads are returned as `application/json`.
- Cross-Origin Resource Sharing (CORS) is enabled (`allow_origins=["*"]`) to permit decoupled frontend development.
- Input validation failures intercepted by custom exception handlers return HTTP 400 Bad Request with a clean `{"detail": "<message>"}` object without leaking internal server stack traces.

---

## 2. API Endpoints Reference

### 2.1 Health Check

#### `GET /api/health`
Checks the operating status and version metadata of the backend simulation service.

- **Method**: `GET`
- **Path**: `/api/health`
- **Authentication**: None
- **Query Parameters**: None
- **Request Body**: None

#### Successful Response (`200 OK`)
```json
{
  "status": "healthy",
  "service": "ARC Page Replacement Simulator API",
  "version": "1.0.0"
}
```

---

### 2.2 Execute Single Algorithm Simulation

#### `POST /api/simulate`
Executes a step-by-step cache replacement simulation for a single selected algorithm (`ARC`, `LRU`, or `FIFO`) against a page reference sequence. Guarantees that execution runs on a fresh, isolated algorithm instance.

- **Method**: `POST`
- **Path**: `/api/simulate`
- **Authentication**: None
- **Headers**: `Content-Type: application/json`

#### Request Schema (`SimulationRequest`)

| Field | Type | Required | Default | Validation Rules / Description |
| :--- | :--- | :---: | :---: | :--- |
| `algorithm` | `string` | **Yes** | — | Name of the algorithm: `"ARC"`, `"LRU"`, or `"FIFO"`. Case-insensitive (`"arc"` and `"lru"` are automatically normalized to uppercase). |
| `capacity` | `integer` | **Yes** | — | Cache capacity $c$. Must be a positive integer $\ge 1$. |
| `reference_string` | `array` | **Yes** | — | Non-empty list of page identifiers (e.g. `[1, 2, 3, 1, 4]`). Elements can be integers or strings. |

#### Request Example
```json
{
  "algorithm": "ARC",
  "capacity": 3,
  "reference_string": [1, 2, 3, 1, 4, 1]
}
```

#### Successful Response (`200 OK`)

Returns cumulative metrics, per-step historical traces (for timeline playback), and the final snapshot of the cache directory.

```json
{
  "algorithm": "ARC",
  "capacity": 3,
  "reference_string": [1, 2, 3, 1, 4, 1],
  "metrics": {
    "total_requests": 6,
    "total_hits": 2,
    "total_misses": 4,
    "total_page_faults": 4,
    "total_replacements": 1,
    "hit_ratio": 0.3333,
    "miss_ratio": 0.6667,
    "page_fault_rate": 0.6667,
    "execution_time_seconds": 0.000312,
    "execution_time_ms": 0.312
  },
  "steps": [
    {
      "step": 1,
      "page": 1,
      "hit": false,
      "hit_type": "MISS",
      "evicted_page": null,
      "movement": "Cache warmup: inserted 1 to T1 (MRU)",
      "state": {
        "capacity": 3,
        "p": 0.0,
        "t1_target": 0.0,
        "t2_target": 3.0,
        "T1": [1],
        "T2": [],
        "B1": [],
        "B2": [],
        "size_T1": 1,
        "size_T2": 0,
        "size_B1": 0,
        "size_B2": 0,
        "size_cache": 1,
        "size_total": 1,
        "requests": 1,
        "hits": 0,
        "misses": 1,
        "hit_ratio": 0.0,
        "replacements": 0,
        "order": "MRU_to_LRU"
      },
      "cumulative_hits": 0,
      "cumulative_misses": 1,
      "cumulative_page_faults": 1,
      "cumulative_hit_ratio": 0.0,
      "cumulative_miss_ratio": 1.0,
      "p": 0.0,
      "T1": [1],
      "T2": [],
      "B1": [],
      "B2": []
    },
    {
      "step": 4,
      "page": 1,
      "hit": true,
      "hit_type": "T1_HIT",
      "evicted_page": null,
      "movement": "Promoted page 1 from T1 to T2 (MRU)",
      "state": {
        "capacity": 3,
        "p": 0.0,
        "t1_target": 0.0,
        "t2_target": 3.0,
        "T1": [3, 2],
        "T2": [1],
        "B1": [],
        "B2": [],
        "size_T1": 2,
        "size_T2": 1,
        "size_B1": 0,
        "size_B2": 0,
        "size_cache": 3,
        "size_total": 3,
        "requests": 4,
        "hits": 1,
        "misses": 3,
        "hit_ratio": 0.25,
        "replacements": 0,
        "order": "MRU_to_LRU"
      },
      "cumulative_hits": 1,
      "cumulative_misses": 3,
      "cumulative_page_faults": 3,
      "cumulative_hit_ratio": 0.25,
      "cumulative_miss_ratio": 0.75,
      "p": 0.0,
      "T1": [3, 2],
      "T2": [1],
      "B1": [],
      "B2": []
    }
  ],
  "final_state": {
    "capacity": 3,
    "p": 0.0,
    "t1_target": 0.0,
    "t2_target": 3.0,
    "T1": [4],
    "T2": [1],
    "B1": [2],
    "B2": [],
    "size_T1": 1,
    "size_T2": 1,
    "size_B1": 1,
    "size_B2": 0,
    "size_cache": 2,
    "size_total": 3,
    "requests": 6,
    "hits": 2,
    "misses": 4,
    "hit_ratio": 0.3333,
    "replacements": 1,
    "order": "MRU_to_LRU"
  }
}
```

#### Error Responses

- **`400 Bad Request`** (Invalid algorithm name):
  ```json
  {
    "detail": "Unsupported algorithm 'RANDOM'. Supported algorithms: ARC, LRU, FIFO"
  }
  ```
- **`400 Bad Request`** (Invalid capacity):
  ```json
  {
    "detail": "Cache capacity must be a positive integer >= 1, got 0"
  }
  ```
- **`422 Unprocessable Entity`** (Missing required field):
  ```json
  {
    "detail": [
      {
        "type": "missing",
        "loc": ["body", "capacity"],
        "msg": "Field required",
        "input": {}
      }
    ]
  }
  ```

---

### 2.3 Comparative Multi-Algorithm Benchmark

#### `POST /api/compare`
Executes ARC, LRU, and FIFO simultaneously across the identical reference string and capacity. Identifies the winner(s) and full ranking based **strictly on minimum page faults**.

- **Method**: `POST`
- **Path**: `/api/compare`
- **Authentication**: None
- **Headers**: `Content-Type: application/json`

#### Request Schema (`ComparisonRequest`)

| Field | Type | Required | Default | Validation Rules / Description |
| :--- | :--- | :---: | :---: | :--- |
| `capacity` | `integer` | **Yes** | — | Cache capacity $c$. Must be a positive integer $\ge 1$. |
| `reference_string` | `array` | **Yes** | — | Sequence of page references (e.g. `[1, 2, 3, 1, 2, 3, 4, 5, 6, 2, 3]`). |
| `include_steps` | `boolean` | No | `false` | If `true`, returns the complete `steps` telemetry array for every algorithm. |

#### Request Example
```json
{
  "capacity": 3,
  "reference_string": [1, 2, 3, 1, 2, 3, 4, 5, 6, 2, 3],
  "include_steps": false
}
```

#### Successful Response (`200 OK`) — Single Winner Case
```json
{
  "capacity": 3,
  "reference_string": [1, 2, 3, 1, 2, 3, 4, 5, 6, 2, 3],
  "results": {
    "ARC": {
      "algorithm": "ARC",
      "capacity": 3,
      "total_requests": 11,
      "hits": 5,
      "misses": 6,
      "page_faults": 6,
      "replacements": 3,
      "hit_ratio": 0.4545,
      "miss_ratio": 0.5455,
      "page_fault_rate": 0.5455,
      "execution_time_ms": 0.412,
      "final_state": {
        "capacity": 3,
        "p": 1.0,
        "t1_target": 1.0,
        "t2_target": 2.0,
        "T1": [6],
        "T2": [3, 2],
        "B1": [5],
        "B2": [4],
        "size_cache": 3,
        "size_total": 5
      }
    },
    "LRU": {
      "algorithm": "LRU",
      "capacity": 3,
      "total_requests": 11,
      "hits": 3,
      "misses": 8,
      "page_faults": 8,
      "replacements": 5,
      "hit_ratio": 0.2727,
      "miss_ratio": 0.7273,
      "page_fault_rate": 0.7273,
      "execution_time_ms": 0.185,
      "final_state": {
        "capacity": 3,
        "pages": [3, 2, 6],
        "size": 3,
        "order": "MRU_to_LRU"
      }
    },
    "FIFO": {
      "algorithm": "FIFO",
      "capacity": 3,
      "total_requests": 11,
      "hits": 3,
      "misses": 8,
      "page_faults": 8,
      "replacements": 5,
      "hit_ratio": 0.2727,
      "miss_ratio": 0.7273,
      "page_fault_rate": 0.7273,
      "execution_time_ms": 0.174,
      "final_state": {
        "capacity": 3,
        "pages": [3, 2, 6],
        "size": 3,
        "order": "MRU_to_LRU"
      }
    }
  },
  "best_algorithm": "ARC",
  "best_algorithms": ["ARC"],
  "is_tie": false,
  "comparison_summary": {
    "ranking": [
      {
        "rank": 1,
        "algorithms": ["ARC"],
        "page_faults": 6,
        "is_tie": false
      },
      {
        "rank": 2,
        "algorithms": ["LRU", "FIFO"],
        "page_faults": 8,
        "is_tie": true
      }
    ],
    "fewest_page_faults": 6,
    "highest_hit_ratio": 0.4545,
    "fastest_execution_ms": 0.174,
    "summary_text": "ARC achieved the best performance with fewest page faults (6 vs LRU: 8, FIFO: 8)."
  }
}
```

#### Successful Response (`200 OK`) — Multi-Algorithm Tie Case
When multiple algorithms achieve the minimum page-fault count (e.g. for `[1, 2, 1, 2]` with $c=2$ where all three have 2 faults):
```json
{
  "best_algorithm": null,
  "best_algorithms": ["ARC", "LRU", "FIFO"],
  "is_tie": true,
  "comparison_summary": {
    "ranking": [
      {
        "rank": 1,
        "algorithms": ["ARC", "LRU", "FIFO"],
        "page_faults": 2,
        "is_tie": true
      }
    ],
    "fewest_page_faults": 2,
    "highest_hit_ratio": 0.5,
    "fastest_execution_ms": 0.112,
    "summary_text": "Tie between ARC, LRU, FIFO with 2 page faults (50.00% hit ratio)."
  }
}
```

---

### 2.4 Workload Generator

#### `POST /api/workload`
Synthesizes deterministic page reference streams based on specified workload models, parameters, and optional random seeds.

- **Method**: `POST`
- **Path**: `/api/workload`
- **Authentication**: None
- **Headers**: `Content-Type: application/json`

#### Request Schema (`WorkloadRequest`)

| Field | Type | Required | Default | Validation Rules / Description |
| :--- | :--- | :---: | :---: | :--- |
| `workload_type` | `string` | **Yes** | — | Workload pattern: `"custom"`, `"sequential"`, `"random"`, `"repeated_locality"`, or `"looping"`. |
| `length` | `integer` | **Yes** | — | Total number of references to generate. Must be an integer $\ge 1$. |
| `num_pages` | `integer` | Conditional | `null` | Total distinct pages in the population. Required for `sequential`, `random`, and `repeated_locality`. |
| `start_page` | `integer` | No | `1` | Lowest page ID integer (default is 1). |
| `seed` | `integer` | No | `null` | Integer random seed for deterministic reproducibility. |
| `hot_pages_count` | `integer` | Conditional | `null` | Number of hot pages ($K$) in the **Two-Tier Bernoulli Partition Model** ($1 \le K \le num\_pages$). |
| `hot_prob` | `float` | No | `0.8` | Probability of selecting a page from the hot set ($0.0 \le p_{\text{hot}} \le 1.0$). |
| `loop_size` | `integer` | Conditional | `null` | Periodicity of the loop ($L$) for `looping` workloads. |
| `loop` | `integer` / `array` | No | `null` | Alternative alias for loop size or an explicit cycle sequence. |
| `pages` | `array` | Conditional | `null` | User-supplied list of page IDs for `"custom"` workloads. |

#### Request Example (Repeated Locality / Two-Tier Bimodal)
```json
{
  "workload_type": "repeated_locality",
  "length": 20,
  "num_pages": 8,
  "hot_pages_count": 2,
  "hot_prob": 0.8,
  "seed": 42
}
```

#### Successful Response (`200 OK`)
```json
{
  "workload_type": "repeated_locality",
  "length": 20,
  "parameters": {
    "num_pages": 8,
    "start_page": 1,
    "seed": 42,
    "hot_pages_count": 2,
    "hot_prob": 0.8
  },
  "reference_string": [1, 2, 2, 1, 1, 2, 6, 2, 2, 1, 2, 2, 2, 1, 1, 2, 1, 2, 5, 1]
}
```

#### Request Example (Sequential Scan)
```json
{
  "workload_type": "sequential",
  "length": 10,
  "num_pages": 4,
  "start_page": 1
}
```

#### Successful Response (`200 OK`)
```json
{
  "workload_type": "sequential",
  "length": 10,
  "parameters": {
    "num_pages": 4,
    "start_page": 1
  },
  "reference_string": [1, 2, 3, 4, 1, 2, 3, 4, 1, 2]
}
```

#### Error Responses
- **`400 Bad Request`** (Invalid workload type):
  ```json
  {
    "detail": "Unknown workload type 'gaussian'. Supported types: 'custom', 'sequential', 'random', 'repeated_locality', 'looping'"
  }
  ```
- **`400 Bad Request`** (Invalid hot_pages_count):
  ```json
  {
    "detail": "hot_pages_count must be between 1 and num_pages (8), got 10"
  }
  ```

---

## 3. Summary of Status Codes

| HTTP Status | Description | Typical Cause |
| :---: | :--- | :--- |
| **`200 OK`** | Request succeeded | Valid parameters; simulation or workload completed successfully. |
| **`400 Bad Request`** | Semantic validation error | Capacity $< 1$, unsupported algorithm name, empty custom sequence, or invalid workload parameters. |
| **`422 Unprocessable Entity`** | Schema validation error | Missing required fields or incorrect data types (e.g. sending a string where an integer is expected). |
| **`500 Internal Server Error`** | Server error | Unexpected unhandled exception (monitored and prevented via exhaustive unit tests). |

