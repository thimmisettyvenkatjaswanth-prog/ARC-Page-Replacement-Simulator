# ARC Page Replacement Simulator: Academic Project Report

**A Comprehensive Comparative Analysis of the Adaptive Replacement Cache (ARC), Least Recently Used (LRU), and First-In, First-Out (FIFO) Page Replacement Algorithms**

---

## 1. Abstract

Cache management is a foundational problem in operating systems and computer architecture, balancing the tradeoff between temporal recency and access frequency to minimize page faults. The Least Recently Used (LRU) algorithm prioritizes recency but is vulnerable to scan pollution and looping thrashing, whereas Least Frequently Used (LFU) prioritizes frequency but suffers from historical accumulation and slow response to locality shifts. The Adaptive Replacement Cache (ARC), introduced by Nimrod Megiddo and Dharmendra S. Modha (USENIX FAST 2003), addresses these shortcomings by dynamically tuning the cache balance between recency and frequency using four interlocking lists and two ghost history caches.

This project implements an Operating Systems laboratory simulator and benchmarking suite for the ARC page replacement policy alongside baseline implementations of LRU and FIFO. The system incorporates an independent algorithmic kernel, a step-by-step simulation engine capturing state transitions and invariants, a synthetic workload generator supporting multiple probability models, a multi-algorithm comparative analysis framework, a FastAPI REST service, and an interactive dark-themed web dashboard. Standardized experimental benchmarking across 120 execution runs demonstrates that ARC achieves superior hit rates in bimodal repeated-locality workloads ($79.97\%$ mean hit ratio for ARC vs. $70.45\%$ for FIFO), ties LRU and FIFO in cyclic looping and sequential scans due to fundamental capacity limits, and performs competitively with baselines under uniform random access where no temporal or frequency structure exists.

---

## 2. Introduction

In modern memory hierarchies, caching serves as the primary mechanism to bridge the latency gap between high-speed volatile storage and higher-latency secondary storage. Operating system virtual memory subsystems rely on page replacement policies to decide which page to evict from physical frames (resident memory) when a page fault occurs and no free frames exist.

The ideal page replacement policy is Belady's Optimal Algorithm (MIN / OPT), which evicts the page that will not be referenced for the longest period into the future. Because MIN requires offline clairvoyance of future memory accesses, practical online algorithms must infer future access patterns from past access history.

Historically, operating systems have relied on heuristics:
- **Recency-based policies** (e.g., LRU) assume that pages referenced recently are likely to be accessed again in the immediate future.
- **Frequency-based policies** (e.g., LFU) assume that pages accessed frequently in the past will maintain high access probabilities.
- **Arrival-order policies** (e.g., FIFO) evict the oldest resident page without considering access recency or frequency.

Both LRU and LFU possess structural failure modes:
1. **Scan Vulnerability**: A sequential scan over a dataset larger than cache capacity flushes the entire cache in LRU, discarding valuable frequently accessed pages.
2. **Frequency Pollution**: In LFU, pages that experienced high reference bursts in an early phase remain pinned in the cache long after their utility has ceased.
3. **Parameter Sensitivity**: Hybrid policies like LRU-2 or 2Q require static threshold tuning (e.g., list size quotas) that fail across diverse or shifting workloads.

In 2003, Nimrod Megiddo and Dharmendra S. Modha proposed the **Adaptive Replacement Cache (ARC)** at the USENIX Conference on File and Storage Technologies (FAST 2003). ARC resolves these limitations through a self-tuning, low-overhead dual-directory architecture that tracks both resident pages and evicted page histories ("ghost caches"), dynamically shifting an internal target parameter $p$ to balance recency and frequency in real time.

This project presents a faithful software realization of ARC, LRU, and FIFO designed to study, visualize, and benchmark page replacement dynamics under controlled experimental conditions.

---

## 3. Problem Statement

Operating systems students and researchers frequently encounter conceptual and practical hurdles when evaluating cache replacement algorithms:
1. **Theoretical Abstraction**: Cache replacement is often taught through short static paper-and-pencil traces (e.g., 10–20 page references), failing to convey how algorithms adapt over extended workloads with hundreds or thousands of accesses.
2. **Implementation Ambiguities**: While LRU and FIFO are straightforward, ARC features nuanced mechanics—such as ghost list tracking, ratio-based learning rate steps ($\delta_1, \delta_2$), fractional target parameters, and the specialized `REPLACE` subroutine boundary conditions—that are rarely exposed in interactive tooling.
3. **Workload Mischaracterization**: Many comparative studies informally describe workloads as "Zipfian" or "random" without verifying the underlying discrete distributions, or report aggregated averages that obscure critical phase transitions such as cache thrashing cliffs.
4. **Lack of Educational Visualization**: Existing simulators either lack modern graphical visualizers or mix simulation logic directly with GUI rendering code, preventing automated testability and scientific verification.

This project addresses these challenges by developing a decoupled, mathematically verified simulation platform that couples a rigorous algorithmic backend with step-by-step state visualization and standardized comparative evaluation.

---

## 4. Objectives

The primary objectives of this project are:
1. **Faithful Algorithmic Realization**:
   - Implement the complete ARC algorithm adhering strictly to the FAST 2003 specification, including the four lists ($T_1, T_2, B_1, B_2$), the floating-point adaptive parameter $p$, the five mathematical invariants, and the `REPLACE` eviction subroutine.
   - Implement standard $O(1)$ baselines for LRU and FIFO using identical interface contracts.
2. **Simulation Telemetry & State Capture**:
   - Construct a reusable simulation engine that executes reference strings against cache instances, producing immutable step-by-step state snapshots, transition classifications, and cumulative metrics.
3. **Synthetic Workload Generation**:
   - Build a workload generator capable of synthesizing custom sequences, sequential scans, uniform random accesses, cyclic loops, and two-tier bimodal locality.
4. **Comparative Analysis Framework**:
   - Provide simultaneous multi-algorithm benchmarking that enforces identical workloads, unpolluted algorithm instances, and page-fault-only winner determination.
5. **Interactive Visualization**:
   - Deliver an interactive web dashboard providing visual list inspection, an interactive parameter $p$ balance bar, step-by-step playback controls, and real-time comparative charts.
6. **Reproducible Experimental Evaluation**:
   - Conduct standardized benchmarks across multiple workload patterns and seeds, recording deterministic page faults and qualifying non-deterministic wall-clock execution times.

---

## 5. Background and Related Concepts

### 5.1 Virtual Memory and Page Replacement Basics

In virtual memory systems, physical memory is partitioned into fixed-size page frames (commonly 4 KB). When an executing process references a virtual address residing on a page not currently mapped to physical RAM, the Memory Management Unit (MMU) triggers a hardware trap known as a **page fault**. The operating system's page fault handler must:
1. Locate the requested page on secondary storage (swap space or filesystem).
2. Find an available page frame in physical memory.
3. If all frames are occupied, invoke a **page replacement algorithm** to select a resident victim page for eviction.
4. If the victim page is dirty, write it back to disk.
5. Load the incoming page into the freed frame, update page tables, and resume process execution.

Because secondary storage access is orders of magnitude slower than main memory (microseconds or milliseconds versus nanoseconds), minimizing the number of page faults ($F$) directly dictates system throughput.

### 5.2 Metrics Defined

For a sequence of $N$ page reference requests $x_1, x_2, \dots, x_N$ serviced by a cache of capacity $c$:
- **Page Fault (Miss)**: An access where $x_t \notin \text{resident cache}$.
- **Page Hit**: An access where $x_t \in \text{resident cache}$.
- **Hit Ratio**:
  $$\text{Hit Ratio} = \frac{\text{Hits}}{N} = \frac{N - F}{N}$$
- **Miss Ratio (Page Fault Rate)**:
  $$\text{Miss Ratio} = \frac{F}{N} = 1 - \text{Hit Ratio}$$
- **Replacements ($R$)**: The number of resident pages evicted to accommodate new pages. For an initially empty cache, $R = \max(0, F - c)$.

### 5.3 Limitations of Classical Algorithms

#### First-In, First-Out (FIFO)
FIFO maintains pages in the chronological order of their arrival into memory. On a page fault in a full cache, the page that entered earliest is evicted.
- **Drawback**: FIFO disregards reference frequency and recency. A heavily used operating system shared library page will be repeatedly evicted simply because it arrived early.
- **Belady's Anomaly**: FIFO exhibits Belady's Anomaly, wherein increasing the physical frame capacity can paradoxically increase the total number of page faults for specific reference sequences.

#### Least Recently Used (LRU)
LRU evicts the page that has not been referenced for the longest duration of time.
- **Stack Property**: LRU is a stack algorithm; the set of pages in a cache of capacity $c$ is always a subset of the pages in a cache of capacity $c + 1$, guaranteeing immunity from Belady's Anomaly.
- **Drawback**: LRU is scan-intolerant. A one-time streaming scan over $M > c$ distinct pages will completely evict all previously cached pages from memory, even if those evicted pages had high long-term access frequencies.

#### 2Q (Two Queue)
Proposed by Johnson and Shasha (VLDB 1994), 2Q attempts to eliminate scan pollution by routing first-time references to a FIFO queue ($A1_{\text{in}}$) and promoting rereferenced pages to an LRU queue ($Am$). However, 2Q requires static configuration of the boundary parameter $K_{\text{in}}$, making it brittle across workloads with fluctuating locality.

---

## 6. ARC Algorithm and Mathematical Foundations

### 6.1 Directory Partitioning and the Four Lists

ARC manages a cache directory tracking up to $2c$ pages for a physical cache capacity of $c$. The directory is partitioned into two disjoint directories of pages:
- **$L_1$ (Recency Directory)**: Tracks pages that have been referenced *only once* recently.
- **$L_2$ (Frequency Directory)**: Tracks pages that have been referenced *at least twice* recently.

Each directory is subdivided into a **top (resident)** list and a **bottom (ghost history)** list:
1. **$T_1$ (Top Recency)**: Contains resident cache pages holding data. Pages in $T_1$ have been referenced once recently. $|T_1| \le c$.
2. **$T_2$ (Top Frequency)**: Contains resident cache pages holding data. Pages in $T_2$ have been referenced at least twice recently. $|T_2| \le c$.
3. **$B_1$ (Bottom Recency / Ghost 1)**: Contains metadata keys (page identifiers) of pages recently evicted from $T_1$. $B_1$ holds no page data; its purpose is historical tracking.
4. **$B_2$ (Bottom Frequency / Ghost 2)**: Contains metadata keys of pages recently evicted from $T_2$. $B_2$ holds no page data.

The active resident cache consists strictly of:
$$\text{Resident Cache} = T_1 \cup T_2, \quad \text{with } |T_1 \cup T_2| \le c$$

The total tracked directory consists of:
$$\text{Total Directory} = L_1 \cup L_2 = (T_1 \cup B_1) \cup (T_2 \cup B_2), \quad \text{with } |L_1 \cup L_2| \le 2c$$

```
+-----------------------------------------------------------------+
|                    ARC DIRECTORY (Capacity 2c)                  |
+--------------------------------+--------------------------------+
|       L1 (Recency History)     |      L2 (Frequency History)    |
+----------------+---------------+----------------+---------------+
|  T1 (Resident) |  B1 (Ghost)   |  T2 (Resident) |  B2 (Ghost)   |
|  [MRU ... LRU] | [MRU ... LRU] | [MRU ... LRU]  | [MRU ... LRU] |
+----------------+---------------+----------------+---------------+
|<--------- Target p ----------->|<-------- Target c - p -------->|
|<========== Active Resident Cache (Capacity c) =================>|
```

### 6.2 The Five Mathematical Invariants

In Section 3 of the FAST 2003 paper, Megiddo and Modha define five structural invariants that must hold before and after every page reference:

1. **Resident Cache Bound**:
   $$|T_1| + |T_2| \le c$$
   The physical cache capacity $c$ is never exceeded.
2. **Total Directory Bound**:
   $$|T_1| + |T_2| + |B_1| + |B_2| \le 2c$$
   The directory stores metadata for at most twice the cache capacity.
3. **Recency Directory Bound**:
   $$|L_1| = |T_1| + |B_1| \le c$$
   The recency directory cannot exceed the physical cache capacity.
4. **Target Parameter Bound**:
   $$0 \le p \le c$$
   The target size for $T_1$ is bounded between 0 and $c$.
5. **Mutual Disjointness**:
   $$T_1 \cap T_2 = \emptyset, \quad B_1 \cap B_2 = \emptyset, \quad L_1 \cap L_2 = (T_1 \cup B_1) \cap (T_2 \cup B_2) = \emptyset$$
   Every tracked page resides in exactly one list.

### 6.3 The Adaptive Target Parameter $p$ and Learning Rules

The parameter $p \in [0, c]$ represents the target resident size for $T_1$, while $c - p$ represents the target resident size for $T_2$. Rather than requiring static operator tuning, $p$ is modified dynamically by learning steps whenever a hit occurs in the ghost caches $B_1$ or $B_2$:

- **Ghost Hit in $B_1$ (Recency History Miss)**:
  A request for $x_t \in B_1$ indicates that the cache recently evicted a page from $T_1$ that has now been requested again. The cache would have achieved a hit had $T_1$ been allocated more capacity. Therefore, ARC shifts $p$ upward to favor recency:
  $$\delta_1 = \begin{cases} 1.0 & \text{if } |B_1| \ge |B_2| \\ \frac{|B_2|}{|B_1|} & \text{if } |B_1| < |B_2| \end{cases}$$
  $$p \leftarrow \min\left(c, p + \delta_1\right)$$

- **Ghost Hit in $B_2$ (Frequency History Miss)**:
  A request for $x_t \in B_2$ indicates that a frequently accessed page was evicted from $T_2$ and rereferenced. The cache should allocate more capacity to frequency. Therefore, ARC shifts $p$ downward to favor frequency:
  $$\delta_2 = \begin{cases} 1.0 & \text{if } |B_2| \ge |B_1| \\ \frac{|B_1|}{|B_2|} & \text{if } |B_2| < |B_1| \end{cases}$$
  $$p \leftarrow \max\left(0, p - \delta_2\right)$$

The learning rates $\delta_1$ and $\delta_2$ are inversely proportional to ghost list sizes, providing high-gain responsiveness when ghost caches are small and stable low-gain adaptation when ghost caches are large.

### 6.4 The REPLACE Eviction Procedure

When a page fault occurs and a resident page must be evicted to free a frame, ARC calls the subroutine `REPLACE(p, x_t)`. The subroutine decides whether to evict from $T_1$ to $B_1$ or from $T_2$ to $B_2$:

```text
Subroutine REPLACE(p, x_t):
    if (|T1| >= 1) and (((x_t in B2) and (|T1| == p)) or (|T1| > p)):
        Delete the LRU page from T1 and prepend it to the MRU of B1
    else:
        Delete the LRU page from T2 and prepend it to the MRU of B2
```

The condition `(x_t in B2 and |T1| == p)` is a crucial boundary rule: when $p$ has just decreased due to a ghost hit in $B_2$ and $|T_1|$ equals the new target $p$, an eviction must come from $T_1$ to preserve the target allocation.

### 6.5 The Four Request Cases

For every incoming page reference $x_t$, ARC executes exactly one of four operational cases:

#### Case 1: Cache Hit ($x_t \in T_1 \cup T_2$)
The page is already resident in memory:
- Record a hit.
- Remove $x_t$ from its current position in $T_1$ or $T_2$.
- Insert $x_t$ at the top (MRU) of $T_2$.
- $p$ remains unchanged.

#### Case 2: Ghost Hit in $B_1$ ($x_t \in B_1$)
A page fault occurs, but the page was recently evicted from $T_1$:
- Record a miss / page fault.
- Adapt target parameter: $p \leftarrow \min(c, p + \delta_1)$.
- Call `REPLACE(p, x_t)` to evict a resident page.
- Remove $x_t$ from $B_1$, fetch it into memory, and insert at the MRU of $T_2$ (it has now been accessed twice).

#### Case 3: Ghost Hit in $B_2$ ($x_t \in B_2$)
A page fault occurs, but the page was recently evicted from $T_2$:
- Record a miss / page fault.
- Adapt target parameter: $p \leftarrow \max(0, p - \delta_2)$.
- Call `REPLACE(p, x_t)` to evict a resident page.
- Remove $x_t$ from $B_2$, fetch it into memory, and insert at the MRU of $T_2$.

#### Case 4: Cache and History Miss ($x_t \notin T_1 \cup T_2 \cup B_1 \cup B_2$)
The requested page has not been observed recently:
- Record a miss / page fault.
- **Case 4A ($|L_1| = c$)**:
  - If $|T_1| < c$: Delete the LRU page of $B_1$ (freeing directory space) and call `REPLACE(p, x_t)`.
  - Else ($|T_1| == c$): Delete the LRU page of $T_1$ directly without placing it into $B_1$.
- **Case 4B ($|L_1| < c$)**:
  - If $|L_1| + |L_2| \ge c$:
    - If $|L_1| + |L_2| == 2c$: Delete the LRU page of $B_2$ (directory is completely full).
    - Call `REPLACE(p, x_t)`.
  - Else: Cache warmup phase ($|T_1| + |T_2| < c$). No replacement needed.
- Fetch $x_t$ into memory and insert at the MRU of $T_1$ (first reference).

---

## 7. LRU and FIFO Algorithms

### 7.1 Least Recently Used (LRU)

The LRU implementation maintains resident pages ordered by access recency.
- **Data Structure**: Python's `collections.OrderedDict`, providing an internal doubly-linked hash map.
- **Operation**:
  - **Hit**: The accessed page is moved to the MRU tail in $O(1)$ time via `move_to_end(page, last=True)`.
  - **Miss**: If $|cache| = c$, the LRU head is evicted in $O(1)$ time via `popitem(last=False)`. The incoming page is inserted at the MRU tail.
- **Complexity**: $O(1)$ time per operation, $O(c)$ memory space.

### 7.2 First-In, First-Out (FIFO)

The FIFO implementation maintains resident pages strictly in arrival sequence.
- **Data Structure**: `collections.OrderedDict`.
- **Operation**:
  - **Hit**: The hit counter increments, but the arrival queue order is **not altered** (unlike LRU).
  - **Miss**: If $|cache| = c$, the oldest arrival at the queue head is evicted via `popitem(last=False)`. The new page is appended at the queue tail.
- **Complexity**: $O(1)$ time per operation, $O(c)$ memory space.

### 7.3 Comparative Theoretical Summary

| Characteristic | FIFO | LRU | ARC |
| :--- | :--- | :--- | :--- |
| **Primary Criterion** | Arrival sequence | Recency of access | Recency + Frequency (Online Balance) |
| **Memory Overhead** | $c$ page IDs | $c$ page IDs | $2c$ page IDs (directory) + float $p$ |
| **Lookup / Update Time** | $O(1)$ | $O(1)$ | $O(1)$ |
| **Scan Resistance** | None | None | High (biased evictions from $T_1$) |
| **Frequency Awareness**| None | Implicit (temporal only) | Explicit ($T_2$ tracking + ghost feedback) |
| **Belady's Anomaly** | Vulnerable | Immune (Stack Algorithm) | Immune in practice |
| **Parameter Tuning** | None | None | Self-tuning ($p$ via ghost hits) |

---

## 8. System Architecture and Design

The system follows a layered, decoupled architecture with strict separation of concerns across four tiers:

```
+-----------------------------------------------------------------------+
|                            PRESENTATION TIER                          |
|             HTML5 / CSS3 Dark Theme / Vanilla ES6 JavaScript          |
|        Chart.js 4.4 / Memory Cards / Playback Controls / Split Bar    |
+-----------------------------------------------------------------------+
                                   |  HTTP / JSON (REST API)
                                   v
+-----------------------------------------------------------------------+
|                             API SERVICE TIER                          |
|         FastAPI / Starlette / Uvicorn / Pydantic v2 Models            |
|   /api/health  |  /api/simulate  |  /api/compare  |  /api/workload     |
+-----------------------------------------------------------------------+
                                   |  Python Calls
                                   v
+-----------------------------------------------------------------------+
|                         APPLICATION & ANALYSIS TIER                   |
|     SimulationEngine            |           CacheComparator           |
| (Step Telemetry, Snapshots)     |  (Multi-Algo Isolation, Tie Logic)  |
+---------------------------------+-------------------------------------+
                                   |  Algorithm Interface: request(),
                                   |  get_state(), reset()
                                   v
+-----------------------------------------------------------------------+
|                          ALGORITHM KERNEL TIER                        |
|   ARCCache (FAST 2003)   |      LRUCache       |      FIFOCache       |
|    OrderedDict x 4       |    OrderedDict      |    OrderedDict       |
|   Invariants & REPLACE   |   Recency Order     |   Arrival Order      |
+-----------------------------------------------------------------------+
```

### 8.1 Presentation Tier
- Developed with semantic HTML5, modern dark-mode CSS3, and vanilla JavaScript (no React, Vue, or heavy frameworks).
- Renders responsive memory cards with LRU/MRU orientation, an interactive adaptive parameter $p$ split bar, step-by-step simulation controls (Play, Pause, Step Next, Step Prev, Reset, Speed Slider), and live Chart.js metrics.

### 8.2 API Service Tier
- Built with FastAPI and Pydantic v2.
- Enforces strict input validation on algorithm names, capacities, and reference strings.
- Intercepts exceptions via custom handlers to return clean HTTP 400 responses without exposing internal stack traces.

### 8.3 Application & Analysis Tier
- **`SimulationEngine`**: Drives execution sequentially over reference strings, capturing per-step telemetry records (hit status, transition type, movement descriptions, cumulative ratios) and deep-copied state snapshots.
- **`CacheComparator`**: Instantiates fresh, isolated algorithm instances and evaluates ARC, LRU, and FIFO over identical parameters. Implements page-fault-only winner and tie determination.
- **`workload`**: Factory functions producing deterministic synthetic page reference streams.

### 8.4 Algorithm Kernel Tier
- Implements `ARCCache`, `LRUCache`, and `FIFOCache`.
- Completely independent of web frameworks, file I/O, or GUI libraries. Each algorithm conforms to a unified interface: `request(page) -> Dict[str, Any]`, `get_state() -> Dict[str, Any]`, and `reset() -> None`.

---

## 9. Implementation Details

### 9.1 Data Structure Selection
All three algorithms leverage Python's `collections.OrderedDict`. In Python 3.7+, standard dictionaries preserve insertion order, but `OrderedDict` provides explicit, constant-time reordering via `move_to_end(key, last=True/False)` and constant-time head/tail pops via `popitem(last=True/False)`. This guarantees $O(1)$ time complexity for all cache operations:
- Membership test (`key in dict`): Average $O(1)$.
- Insertion / Promotion (`move_to_end`): $O(1)$.
- Eviction (`popitem`): $O(1)$.

### 9.2 Floating-Point ARC Adaptation vs. Integer Mode
`ARCCache` defaults to `use_integer_p = False`, implementing the exact floating-point learning rates published in Megiddo & Modha (FAST 2003, Figure 4):
$$\delta_1 = \max(1.0, |B_2| / |B_1|), \quad \delta_2 = \max(1.0, |B_1| / |B_2|)$$
The class also supports an optional `use_integer_p = True` variant using integer floor division (`//`), which emulates kernel-level implementations (such as OpenZFS ARC) that operate in environments where floating-point math is restricted.

### 9.3 Invariant Self-Checking
The ARC implementation includes an internal `_verify_invariants()` method invoked at the completion of every `request()` call. It asserts all five mathematical invariants, guaranteeing that bugs in list movement or boundary clamping cause immediate assertion errors during testing.

### 9.4 Workload Generator Distributions
The workload generator module (`backend/simulation/workload.py`) provides four strategies:
1. **`generate_sequential(length, num_pages, start_page)`**: Generates $[start\_page + (i \pmod{num\_pages}) \text{ for } i \in [0, length-1]]$.
2. **`generate_looping(length, loop, start_page)`**: Generates periodic loops of length $loop$.
3. **`generate_random(length, num_pages, start_page, seed)`**: Draws from a Discrete Uniform distribution over $[start\_page, start\_page + num\_pages - 1]$.
4. **`generate_repeated_locality(length, num_pages, hot_pages_count, hot_prob, seed)`**: Implements a **Two-Tier Bernoulli Partition (80/20 Bimodal)** model. A hot set of $K = 8$ pages receives $80\%$ of references (selected uniformly within the hot set), while the remaining $M - K = 56$ cold pages receive $20\%$ (selected uniformly within the cold set).

### 9.5 Testing Strategy
The project includes 91 automated tests across six test suites in `backend/tests/`:
- `test_arc.py` (21 tests): Verifies all four cases, ghost hits, $\delta$ adaptations, fractional $p$, the `REPLACE` boundary condition, edge capacities ($c=1$), reset behavior, and randomized stress invariants.
- `test_lru.py` (12 tests): Validates recency promotions, LRU evictions, hits, and capacity constraints.
- `test_fifo.py` (12 tests): Validates arrival-order preservation on hits and oldest-arrival evictions.
- `test_simulation.py` (15 tests): Tests engine telemetry capture, workload determinism, and zero-division handling.
- `test_comparator.py` (10 tests): Verifies multi-algorithm execution isolation, ranking correctness, and strict page-fault-only tie logic.
- `test_api.py` (19 tests): Tests all HTTP routes, Pydantic validations, 400 error handlers, and static file mounts.
- `test_environment.py` (2 tests): Verifies package structure and Python environment sanity.

---

## 10. Experimental Evaluation and Results

Standardized benchmarking was conducted using the automated suite (`scripts/run_benchmarks.py`). All experiments evaluated ARC, LRU, and FIFO over identical parameters:
- **Reference String Length ($N$)**: $1,000$ requests.
- **Cache Capacity ($c$)**: $16$ pages.
- **Total Executions**: $40 \text{ trials} \times 3 \text{ algorithms} = 120 \text{ algorithmic runs}$.

Winner determination is based **strictly on minimum page faults**; secondary metrics (hits, replacements, execution time) are never used as tie-breakers.

### 10.1 Sequential Scan Workload (Disaggregated by Working Set $M$)

Sequential scans evaluate cache behavior across 10 working-set sizes $M \in \{4, 8, 12, 16, 20, 24, 32, 48, 64, 96\}$:

| Working Set ($M$) | ARC Faults | LRU Faults | FIFO Faults | Hits | Hit Ratio | Replacements | Result |
| :---: | :---: | :---: | :---: | :---: | :---: | :---: | :---: |
| **$M = 4$** | 4 | 4 | 4 | 996 | 99.60% | 0 | 3-Way Tie |
| **$M = 8$** | 8 | 8 | 8 | 992 | 99.20% | 0 | 3-Way Tie |
| **$M = 12$** | 12 | 12 | 12 | 988 | 98.80% | 0 | 3-Way Tie |
| **$M = 16$** ($M = c$) | 16 | 16 | 16 | 984 | 98.40% | 0 | 3-Way Tie |
| **$M = 20$** ($M > c$) | 1,000 | 1,000 | 1,000 | 0 | 0.00% | 984 | 3-Way Tie |
| **$M = 24$** ($M > c$) | 1,000 | 1,000 | 1,000 | 0 | 0.00% | 984 | 3-Way Tie |
| **$M = 32$** ($M > c$) | 1,000 | 1,000 | 1,000 | 0 | 0.00% | 984 | 3-Way Tie |
| **$M = 48$** ($M > c$) | 1,000 | 1,000 | 1,000 | 0 | 0.00% | 984 | 3-Way Tie |
| **$M = 64$** ($M > c$) | 1,000 | 1,000 | 1,000 | 0 | 0.00% | 984 | 3-Way Tie |
| **$M = 96$** ($M > c$) | 1,000 | 1,000 | 1,000 | 0 | 0.00% | 984 | 3-Way Tie |

**Analysis**:
- For $M \le c = 16$, the entire working set fits into the cache. All algorithms experience exactly $M$ initial compulsory misses followed by $1000 - M$ consecutive hits.
- For $M > c$, pages are referenced strictly in sequence without intermediate rereferences. By the time page $k$ is referenced again, $M - 1 \ge c$ other distinct pages have intervened, evicting page $k$ under all policies. Thus, all three algorithms suffer 100% page faults ($1,000$ faults, $0\%$ hit ratio).
- Averaging these 10 trials together produces an uninformative $604.0$ faults, which obscures this sharp binary capacity threshold.

### 10.2 Cyclic Looping Workload (Disaggregated by Loop Size $L$)

Cyclic looping evaluates the cache thrashing cliff across 10 loop sizes $L \in \{4, 8, 12, 15, 16, 17, 18, 20, 24, 32\}$:

| Loop Size ($L$) | ARC Faults | LRU Faults | FIFO Faults | Hits | Hit Ratio | Replacements | Result |
| :---: | :---: | :---: | :---: | :---: | :---: | :---: | :---: |
| **$L = 4$** | 4 | 4 | 4 | 996 | 99.60% | 0 | 3-Way Tie |
| **$L = 8$** | 8 | 8 | 8 | 992 | 99.20% | 0 | 3-Way Tie |
| **$L = 12$** | 12 | 12 | 12 | 988 | 98.80% | 0 | 3-Way Tie |
| **$L = 15$** | 15 | 15 | 15 | 985 | 98.50% | 0 | 3-Way Tie |
| **$L = 16$** ($L = c$) | 16 | 16 | 16 | 984 | 98.40% | 0 | 3-Way Tie |
| **$L = 17$** ($L = c + 1$) | 1,000 | 1,000 | 1,000 | 0 | 0.00% | 984 | 3-Way Tie |
| **$L = 18$** | 1,000 | 1,000 | 1,000 | 0 | 0.00% | 984 | 3-Way Tie |
| **$L = 20$** | 1,000 | 1,000 | 1,000 | 0 | 0.00% | 984 | 3-Way Tie |
| **$L = 24$** | 1,000 | 1,000 | 1,000 | 0 | 0.00% | 984 | 3-Way Tie |
| **$L = 32$** | 1,000 | 1,000 | 1,000 | 0 | 0.00% | 984 | 3-Way Tie |

**Analysis**:
- This workload isolates the **cache thrashing cliff**:
  - At $L = 16 = c$, all algorithms achieve a $98.4\%$ hit ratio ($16$ compulsory misses, $984$ hits).
  - At $L = 17 = c + 1$, increasing the loop by a single page beyond capacity causes the hit ratio to collapse from $98.4\%$ directly to **$0.0\%$** ($1,000$ page faults).
- All three algorithms tie because a pure periodic loop without rereferencing skew causes FIFO, LRU, and ARC to make identical victim selections.

### 10.3 Uniform Random Workload (10 Seeds: 101–110, $M = 64$)

Evaluated over 10 deterministic random seeds with $M = 64$ pages:

| Seed | ARC Faults | LRU Faults | FIFO Faults | Winner(s) |
| :---: | :---: | :---: | :---: | :---: |
| **101** | **747** | 755 | 757 | ARC |
| **102** | **736** | 749 | 764 | ARC |
| **103** | 780 | 771 | **768** | FIFO |
| **104** | 763 | 753 | **740** | FIFO |
| **105** | 755 | **750** | 755 | LRU |
| **106** | 758 | 739 | **726** | FIFO |
| **107** | **764** | 765 | 767 | ARC |
| **108** | 748 | **735** | **735** | LRU, FIFO (Tie) |
| **109** | **741** | 749 | 745 | ARC |
| **110** | 756 | **749** | 750 | LRU |

#### Aggregate Statistics Across Seeds

| Algorithm | Mean Faults | Std Dev | Min Faults | Max Faults | Mean Hit % | Total Wins | Sole Wins | Tied Wins |
| :---: | :---: | :---: | :---: | :---: | :---: | :---: | :---: | :---: |
| **ARC** | 754.8 | 12.69 | 736 | 780 | 24.52% | **4** | **4** | 0 |
| **LRU** | 751.5 | 10.68 | 735 | 771 | 24.85% | 3 | 2 | 1 |
| **FIFO** | **750.7** | 14.16 | **726** | **768** | **24.93%** | **4** | 3 | 1 |

**Analysis**:
- Under uniform random references without temporal clustering or frequency reuse, all three algorithms exhibit nearly identical performance: their mean hit ratios range from $24.52\%$ to $24.93\%$, a difference of approximately $0.41$ percentage points. Across $1,000$ requests, the mean page-fault counts differ by only $4.1$ faults between the best (FIFO at $750.7$) and worst (ARC at $754.8$) averages.
- Across the 10 seeds, ARC won 4 trials (all sole wins), FIFO won 4 trials (3 sole, 1 tie), and LRU won 3 trials (2 sole, 1 tie).
- These close outcomes confirm that under truly independent uniform random access, no replacement policy provides a systematic advantage over the others.

### 10.4 Repeated Locality Workload (10 Seeds: 201–210, Two-Tier Bimodal 80/20)

Evaluated over 10 deterministic seeds using the Two-Tier Bernoulli Partition ($p_{\text{hot}} = 0.8$, $K = 8$, $M = 64$):

| Seed | ARC Faults | LRU Faults | FIFO Faults | Winner(s) |
| :---: | :---: | :---: | :---: | :---: |
| **201** | 173 | **170** | 255 | LRU |
| **202** | **209** | 226 | 311 | ARC |
| **203** | **192** | 195 | 289 | ARC |
| **204** | **195** | 196 | 301 | ARC |
| **205** | **212** | **212** | 315 | ARC, LRU (Tie) |
| **206** | **217** | 222 | 311 | ARC |
| **207** | **209** | 212 | 299 | ARC |
| **208** | **203** | **203** | 294 | ARC, LRU (Tie) |
| **209** | **213** | 215 | 309 | ARC |
| **210** | 180 | **178** | 271 | LRU |

#### Aggregate Statistics Across Seeds

| Algorithm | Mean Faults | Std Dev | Min Faults | Max Faults | Mean Hit % | Total Wins | Sole Wins | Tied Wins |
| :---: | :---: | :---: | :---: | :---: | :---: | :---: | :---: | :---: |
| **ARC** | **200.3** | **14.87** | 173 | **217** | **79.97%** | **8** | **6** | 2 |
| **LRU** | 202.9 | 18.33 | **170** | 226 | 79.71% | 4 | 2 | 2 |
| **FIFO** | 295.5 | 19.35 | 255 | 315 | 70.45% | 0 | 0 | 0 |

**Empirical Measured Results**:
- ARC achieved the lowest mean page faults (**$200.3$**) and highest mean hit ratio (**$79.97\%$**).
- ARC won **8 out of 10 trials** (6 sole wins, 2 tied wins with LRU).
- LRU was competitive, averaging $202.9$ faults ($79.71\%$ hit ratio) and winning 4 trials (2 sole wins, 2 tied wins).
- FIFO exhibited substantially degraded performance, averaging $295.5$ faults ($47.5\%$ more faults than ARC) and winning 0 trials.

**Theoretical Interpretation and Context**:
- *Distinction between measurement and interpretation*: While the benchmark measurements objectively demonstrate ARC's lower page-fault counts and higher win rate in this workload, attributing this performance difference to specific internal list mechanics represents an architectural interpretation rather than a directly isolated causal proof.
- *Consistency with ARC's design*: A plausible interpretation consistent with ARC's frequency-based adaptation and $T_2$ protection is that its dual-list structure ($T_1$ for recency, $T_2$ for frequency) and adaptive target parameter $p$ provide a mechanism to shield frequently accessed pages from eviction. As pages in the hot set receive repeated references, they qualify for promotion into $T_2$. When cold pages arrive intermittently, evictions directed toward $T_1$ (subject to $p$) can avoid evicting hot pages resident in $T_2$.
- *Contrast with FIFO*: FIFO evicts strictly by arrival sequence without regard to access frequency, naturally evicting hot pages whenever bursts of cold references arrive.
- *Comparison with LRU*: LRU also retains frequently accessed pages effectively if they are rereferenced before reaching the eviction tail, which is consistent with LRU's close competitive performance ($202.9$ vs $200.3$ mean faults). However, ARC's ghost cache history ($B_1, B_2$) enables dynamic rebalancing of recency versus frequency capacity, providing an adaptive heuristic that aligns well with the observed empirical advantage in this bimodal access pattern.

### 10.5 Execution Time Qualification

In all experiments, elapsed wall-clock execution time was recorded using `time.perf_counter()`. Across the 120 algorithmic runs, execution times typically averaged:
- FIFO: $9.0$–$13.0$ ms per 1,000 requests.
- LRU: $9.5$–$13.5$ ms per 1,000 requests.
- ARC: $16.0$–$23.0$ ms per 1,000 requests.

**Important Qualification**: Wall-clock execution time is **environment-dependent** and subject to host CPU frequency scaling, operating system background scheduling, and Python interpreter garbage collection. While fixed seeds guarantee 100% exact reproducibility of page faults, hits, and replacements across platforms, execution times vary between runs.

---

## 11. Limitations

To maintain academic rigor, the following system limitations are explicitly documented:

1. **Simulation Scope**:
   This project is a software simulator operating on abstract page identifiers (integers or strings). It does not implement physical hardware page tables, Translation Lookaside Buffer (TLB) management, dirty-bit writeback disk queues, multi-threaded cache locks, or real kernel memory page faults.
2. **Directory Memory Overhead**:
   ARC tracks a directory of up to $2c$ entries, requiring twice the metadata storage of standard LRU. In systems with massive page directories (e.g., millions of pages), this metadata footprint can represent a non-negligible memory commitment.
3. **Pure Looping Vulnerability**:
   ARC does not prevent thrashing under pure cyclic loops of size $L > c$ without skew, as demonstrated in the cyclic looping benchmark where all three algorithms experienced 100% miss rates. Algorithms specifically designed for loop detection (e.g., CAR, CART, or LIRS) are required to capture loop residency.
4. **Single-Threaded Simulation**:
   The simulator executes sequentially within a single thread. It does not measure lock contention or concurrent throughput under multi-core workloads.

---

## 12. Conclusion and Future Enhancements

### 12.1 Conclusion
This project developed, validated, and evaluated a faithful implementation of the Adaptive Replacement Cache (ARC) algorithm alongside LRU and FIFO baselines. The implementation satisfies all five paper invariants, implements exact floating-point learning rates, and captures comprehensive simulation telemetry.

Our standardized experimental evaluation confirms that:
- ARC delivers substantial page-fault reductions over FIFO and modest gains over LRU in workloads exhibiting skewed bimodal locality ($200.3$ faults vs. $295.5$ for FIFO).
- In workloads lacking temporal or frequency structure (uniform random), all three algorithms perform nearly identically (mean hit ratios differing by only 0.41 percentage points).
- In sequential scans and cyclic loops exceeding capacity, all three policies suffer complete cache misses due to fundamental capacity limits.
- The simulator provides an effective laboratory tool for analyzing page replacement dynamics.

### 12.2 Future Enhancements
Potential avenues for future expansion include:
1. **Clock-Based ARC (CAR)**: Implementing CAR (Megiddo & Modha, FAST 2004), which replaces the $O(1)$ doubly-linked lists with clock hands to reduce synchronization overhead in operating system kernels.
2. **Trace Replay Engine**: Adding parsers for standard industry trace formats (e.g., UMass storage traces, ARC benchmark traces) to evaluate algorithms against real-world storage I/O streams.
3. **Multi-Threaded Concurrency Testing**: Introducing fine-grained locking or lock-free data structures to benchmark throughput under concurrent multi-threaded access.
4. **Additional Baselines**: Incorporating LIRS (Low Inter-reference Recency Set), 2Q, and CLOCK-Pro for broader academic comparisons.

---

## 13. References

1. **Megiddo, N., & Modha, D. S.** (2003). "ARC: A Self-Tuning, Low Overhead Replacement Cache." *Proceedings of the 2nd USENIX Conference on File and Storage Technologies (FAST '03)*, pp. 115–130.
2. **Megiddo, N., & Modha, D. S.** (2004). "Outperforming LRU with an Adaptive Replacement Cache Algorithm." *IEEE Computer*, 37(4), pp. 58–65.
3. **Bélády, L. A.** (1966). "A Study of Replacement Algorithms for a Virtual-Storage Computer." *IBM Systems Journal*, 5(2), pp. 78–101.
4. **Johnson, T., & Shasha, D.** (1994). "2Q: A Low Overhead High Performance Buffer Management Replacement Algorithm." *Proceedings of the 20th International Conference on Very Large Data Bases (VLDB)*, pp. 439–450.
5. **Silberschatz, A., Galvin, P. B., & Gagne, G.** (2018). *Operating System Concepts* (10th ed.). John Wiley & Sons.

