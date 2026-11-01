# Standardized Benchmarking Methodology & Experimental Evaluation

## 1. Overview & Objective

This document provides a comprehensive, rigorous experimental evaluation of the **Adaptive Replacement Cache (ARC)** against the foundational baselines **Least Recently Used (LRU)** and **First-In, First-Out (FIFO)**.

All experiments were executed using the production implementation without modifications or mockups:
- **ARC**: `backend.algorithms.arc.ARCCache` (FAST 2003 faithful floating-point learning rate adaptation)
- **LRU**: `backend.algorithms.lru.LRUCache` (recency-based eviction)
- **FIFO**: `backend.algorithms.fifo.FIFOCache` (arrival-order eviction)
- **Simulation Engine**: `backend.simulation.engine.SimulationEngine`
- **Workload Generator**: `backend.simulation.workload.generate_workload`
- **Comparative Evaluator**: `backend.analysis.comparator.compare_algorithms`

---

## 2. Experimental Design & Parameters

A total of **40 standardized trials** were executed across four distinct access patterns (10 trials per workload). For every trial, ARC, LRU, and FIFO were evaluated on the **identical reference string** using **fresh, isolated instances**.

### Fixed System Constants
- **Reference String Length ($N$)**: $1,000$ requests per trial.
- **Cache Capacity ($c$)**: $16$ pages.
- **Total Algorithmic Runs**: $40 \text{ trials} \times 3 \text{ algorithms} = 120 \text{ executions}$.

---

## 3. Workload Generation & Verified Probability Distributions

### Workload 1: Sequential Scan (`sequential`)
- **Generator Function**: `generate_sequential(length=1000, num_pages=M, start_page=1)`
- **Mathematical Sequence**:
  $$x_i = 1 + (i \pmod M), \quad i \in \{0, 1, \dots, N-1\}$$
- **Access Pattern**: Repeated sequential scanning across $M$ distinct pages ($1, 2, \dots, M, 1, 2, \dots, M$).
- **Evaluated Configurations (10 working-set sizes)**:
  $M \in \{4, 8, 12, 16, 20, 24, 32, 48, 64, 96\}$.
- **Experimental Purpose**: Evaluates cache scan resistance and behavior below capacity ($M \le c$) versus beyond capacity ($M > c$).
- **Reporting Rule**: Because performance depends critically on whether $M \le c$ or $M > c$, each working-set size is reported **individually**. They are **not** averaged into a single aggregate metric.

### Workload 2: Cyclic Looping (`looping`)
- **Generator Function**: `generate_looping(length=1000, loop=L, start_page=1)`
- **Mathematical Sequence**:
  $$x_i = 1 + (i \pmod L), \quad i \in \{0, 1, \dots, N-1\}$$
- **Access Pattern**: Strict periodic looping across $L$ pages ($1, \dots, L, 1, \dots, L$).
- **Evaluated Configurations (10 loop sizes)**:
  $L \in \{4, 8, 12, 15, 16, 17, 18, 20, 24, 32\}$.
- **Experimental Purpose**: Directly benchmarks the critical cache thrashing boundary at $L = c + 1 = 17$, comparing behavior when the working set fits completely ($L \le 16$) versus when it exceeds capacity by even a single page ($L \ge 17$).
- **Reporting Rule**: Each loop size is reported **individually** to reveal the sharp phase transition at $L = 17$. They are **not** combined into a single aggregate average.

### Workload 3: Uniform Random (`random`)
- **Generator Function**: `generate_random(length=1000, num_pages=64, start_page=1, seed=seed)`
- **Mathematical Distribution**: Discrete Uniform Distribution over $M = 64$ pages:
  $$P(X_i = k) = \frac{1}{64}, \quad \forall k \in \{1, 2, \dots, 64\}$$
- **Evaluated Configurations**: 10 independent trials using deterministic random seeds:
  `Seeds = [101, 102, 103, 104, 105, 106, 107, 108, 109, 110]`.
- **Experimental Purpose**: Tests replacement behavior in the complete absence of temporal or frequency locality.
- **Reporting Rule**: All 10 trials use identical structural parameters ($N=1000, c=16, M=64$). Results are reported per seed and summarized across seeds using sample mean, standard deviation, minimum, and maximum page faults.

### Workload 4: Repeated Locality (`repeated_locality`)
- **Generator Function**: `generate_repeated_locality(length=1000, num_pages=64, hot_pages_count=8, hot_prob=0.8, seed=seed)`
- **Verified Distribution Analysis (Two-Tier Bimodal / 80-20 Bernoulli Partition)**:
  Inspection of `backend/simulation/workload.py` reveals that this workload does **not** generate a continuous Zipf-Mandelbrot power-law distribution ($P(k) \propto 1/k^s$). Instead, it implements a **Two-Tier Bernoulli Partition Model**:
  1. The working set of $M = 64$ pages is partitioned into two disjoint subsets:
     - **Hot Set ($S_{\text{hot}}$)**: $K = 8$ pages (IDs $1, \dots, 8$).
     - **Cold Set ($S_{\text{cold}}$)**: $M - K = 56$ pages (IDs $9, \dots, 64$).
  2. For each request, an independent Bernoulli trial determines whether the access is hot ($p_{\text{hot}} = 0.8$) or cold ($1 - p_{\text{hot}} = 0.2$):
     - If hot, a page is chosen **uniformly at random** from $S_{\text{hot}}$:
       $$P(X_i = k \mid k \in S_{\text{hot}}) = \frac{p_{\text{hot}}}{K} = \frac{0.80}{8} = 0.1000 \quad (10.0\% \text{ per hot page})$$
     - If cold, a page is chosen **uniformly at random** from $S_{\text{cold}}$:
       $$P(X_i = k \mid k \in S_{\text{cold}}) = \frac{1 - p_{\text{hot}}}{M - K} = \frac{0.20}{56} \approx 0.00357 \quad (0.357\% \text{ per cold page})$$
  3. This generates a bimodal probability mass function where each hot page is approximately $28\times$ more likely to be accessed than any cold page, approximating the 80/20 heuristic.
- **Evaluated Configurations**: 10 independent trials using deterministic random seeds:
  `Seeds = [201, 202, 203, 204, 205, 206, 207, 208, 209, 210]`.
- **Reporting Rule**: Reported per seed and summarized across seeds using sample mean, standard deviation, minimum, and maximum page faults.

---

## 4. Metrics & Winner Calculation Methodology

- **Page Faults ($F$)**: Number of requests where the page was not present in the resident cache.
- **Hits ($H$)**: Number of requests satisfied by the resident cache ($H = N - F$).
- **Hit Ratio**: $\frac{H}{N}$ (percentage of requests satisfied from cache).
- **Miss Ratio**: $\frac{F}{N} = 1 - \text{Hit Ratio}$.
- **Replacements ($R$)**: Number of page evictions executed when inserting into a full resident cache.
- **Execution Time (ms)**: High-resolution wall-clock elapsed time measured via `time.perf_counter()`.
- **Winner Determination Rule**:
  $$\text{Winner} = \arg\min_{\text{algo} \in \{\text{ARC}, \text{LRU}, \text{FIFO}\}} (F)$$
  - **Sole Winner**: Exactly one algorithm achieves the minimum page faults (`is_sole_winner = True, is_tie = False`).
  - **Tied Winners**: Multiple algorithms achieve the identical minimum page faults. All tied algorithms are recorded as winners (`is_tie = True`).
  - **Strict Tie Rule**: Page faults are the **exclusive** criterion for victory. Secondary metrics (hits, replacements, execution time) are **never** used as tie-breakers.

---

## 5. Execution Time Qualification & Reproducibility Limitations

- **Deterministic Outcomes**: Because the algorithms, initial cache states, and synthetic sequences are fully deterministic (with random workloads governed by fixed integer seeds), the page faults, hits, hit ratios, and replacements are **100% reproducible** across platforms and runs.
- **Execution Time Limitations**: Execution times represent real wall-clock elapsed time measured on the host system. Execution times are subject to OS thread scheduling, CPU frequency scaling, Python garbage collection, and system background load. Consequently, while execution times are recorded in all CSV outputs for transparency, they are **environment-dependent and non-deterministic**.

---

## 6. Experimental Results & Analysis

### 6.1 Sequential Scan Workload (Disaggregated by Working Set $M$)

Cache Capacity $c = 16$, Length $N = 1000$:

| Working Set ($M$) | Algorithm | Page Faults | Hits | Hit Ratio | Replacements | Result |
| :---: | :---: | :---: | :---: | :---: | :---: | :---: |
| **$M = 4$** | ARC / LRU / FIFO | **4** | 996 | 99.60% | 0 | 3-Way Tie |
| **$M = 8$** | ARC / LRU / FIFO | **8** | 992 | 99.20% | 0 | 3-Way Tie |
| **$M = 12$** | ARC / LRU / FIFO | **12** | 988 | 98.80% | 0 | 3-Way Tie |
| **$M = 16$** ($M = c$) | ARC / LRU / FIFO | **16** | 984 | 98.40% | 0 | 3-Way Tie |
| **$M = 20$** ($M > c$) | ARC / LRU / FIFO | **1,000** | 0 | 0.00% | 984 | 3-Way Tie |
| **$M = 24$** ($M > c$) | ARC / LRU / FIFO | **1,000** | 0 | 0.00% | 984 | 3-Way Tie |
| **$M = 32$** ($M > c$) | ARC / LRU / FIFO | **1,000** | 0 | 0.00% | 984 | 3-Way Tie |
| **$M = 48$** ($M > c$) | ARC / LRU / FIFO | **1,000** | 0 | 0.00% | 984 | 3-Way Tie |
| **$M = 64$** ($M > c$) | ARC / LRU / FIFO | **1,000** | 0 | 0.00% | 984 | 3-Way Tie |
| **$M = 96$** ($M > c$) | ARC / LRU / FIFO | **1,000** | 0 | 0.00% | 984 | 3-Way Tie |

**Findings**:
- When $M \le c$ ($M \le 16$), the entire working set fits into the resident cache. All algorithms experience exactly $M$ initial compulsory misses followed by $1000 - M$ consecutive hits.
- When $M > c$ ($M \ge 20$), each access to a page occurs after $M - 1$ intervening distinct page requests. Because $M - 1 \ge c$, the requested page has always been evicted by the time it is rereferenced, causing 100% cache misses ($1,000$ faults, $0\%$ hit ratio) across all three policies.
- Averaging across all 10 configurations previously produced $604.0$ faults, which obscured this critical binary threshold behavior.

---

### 6.2 Cyclic Looping Workload (Disaggregated by Loop Size $L$)

Cache Capacity $c = 16$, Length $N = 1000$:

| Loop Size ($L$) | Algorithm | Page Faults | Hits | Hit Ratio | Replacements | Result |
| :---: | :---: | :---: | :---: | :---: | :---: | :---: |
| **$L = 4$** | ARC / LRU / FIFO | **4** | 996 | 99.60% | 0 | 3-Way Tie |
| **$L = 8$** | ARC / LRU / FIFO | **8** | 992 | 99.20% | 0 | 3-Way Tie |
| **$L = 12$** | ARC / LRU / FIFO | **12** | 988 | 98.80% | 0 | 3-Way Tie |
| **$L = 15$** | ARC / LRU / FIFO | **15** | 985 | 98.50% | 0 | 3-Way Tie |
| **$L = 16$** ($L = c$) | ARC / LRU / FIFO | **16** | 984 | 98.40% | 0 | 3-Way Tie |
| **$L = 17$** ($L = c + 1$) | ARC / LRU / FIFO | **1,000** | 0 | 0.00% | 984 | 3-Way Tie |
| **$L = 18$** | ARC / LRU / FIFO | **1,000** | 0 | 0.00% | 984 | 3-Way Tie |
| **$L = 20$** | ARC / LRU / FIFO | **1,000** | 0 | 0.00% | 984 | 3-Way Tie |
| **$L = 24$** | ARC / LRU / FIFO | **1,000** | 0 | 0.00% | 984 | 3-Way Tie |
| **$L = 32$** | ARC / LRU / FIFO | **1,000** | 0 | 0.00% | 984 | 3-Way Tie |

**Findings**:
- Demonstrates the classic **cache thrashing cliff**:
  - At $L = 16 = c$, all algorithms experience $16$ compulsory misses and $984$ hits ($98.4\%$ hit ratio).
  - At $L = 17 = c + 1$, increasing the loop by just one page beyond cache capacity causes hit ratio to plummet from **$98.4\%$ directly to $0.0\%$** ($1,000$ page faults).
- All three algorithms tie across all 10 loop sizes because pure cyclic loops without rereferencing skew produce identical FIFO, LRU, and ARC eviction choices.

---

### 6.3 Uniform Random Workload (10 Seeds: 101–110, $M = 64$, $c = 16$, $N = 1000$)

#### Individual Seed Results

| Seed | ARC Faults | LRU Faults | FIFO Faults | Winner(s) | Tie? |
| :---: | :---: | :---: | :---: | :---: | :---: |
| **101** | **747** | 755 | 757 | **ARC** | No |
| **102** | **736** | 749 | 764 | **ARC** | No |
| **103** | 780 | 771 | **768** | **FIFO** | No |
| **104** | 763 | 753 | **740** | **FIFO** | No |
| **105** | 755 | **750** | 755 | **LRU** | No |
| **106** | 758 | 739 | **726** | **FIFO** | No |
| **107** | **764** | 765 | 767 | **ARC** | No |
| **108** | 748 | **735** | **735** | **LRU, FIFO** | Yes |
| **109** | **741** | 749 | 745 | **ARC** | No |
| **110** | 756 | **749** | 750 | **LRU** | No |

#### Aggregate Statistics Across Seeds

| Algorithm | Mean Faults | Std Dev | Min Faults | Max Faults | Mean Hit % | Total Wins | Sole Wins | Tied Wins |
| :---: | :---: | :---: | :---: | :---: | :---: | :---: | :---: | :---: |
| **ARC** | 754.8 | 12.69 | 736 | 780 | 24.52% | **4** | **4** | 0 |
| **LRU** | 751.5 | 10.68 | 735 | 771 | 24.85% | 3 | 2 | 1 |
| **FIFO** | **750.7** | 14.16 | **726** | **768** | **24.93%** | **4** | 3 | 1 |

**Findings**:
- Under uniform random references without temporal clustering or frequency reuse, all three algorithms exhibit nearly identical performance: their mean hit ratios range from $24.52\%$ to $24.93\%$, a difference of approximately $0.41$ percentage points. Across $1,000$ requests, the mean page-fault counts differ by only $4.1$ faults between the best (FIFO at $750.7$) and worst (ARC at $754.8$) averages.
- Win distribution across the 10 seeds: ARC won 4 trials (all sole wins), FIFO won 4 trials (3 sole, 1 tie), and LRU won 3 trials (2 sole, 1 tie).
- While FIFO achieved a marginally lower average fault count ($750.7$ vs $754.8$ for ARC), ARC achieved the highest number of sole-winner trials ($4$ vs $3$ for FIFO and $2$ for LRU). These close results confirm that under truly independent uniform random access, no replacement policy provides a systematic advantage over the others.

---

### 6.4 Repeated Locality Workload (Two-Tier Bimodal 80/20, 10 Seeds: 201–210, $c = 16$, $N = 1000$)

#### Individual Seed Results

| Seed | ARC Faults | LRU Faults | FIFO Faults | Winner(s) | Tie? |
| :---: | :---: | :---: | :---: | :---: | :---: |
| **201** | 173 | **170** | 255 | **LRU** | No |
| **202** | **209** | 226 | 311 | **ARC** | No |
| **203** | **192** | 195 | 289 | **ARC** | No |
| **204** | **195** | 196 | 301 | **ARC** | No |
| **205** | **212** | **212** | 315 | **ARC, LRU** | Yes |
| **206** | **217** | 222 | 311 | **ARC** | No |
| **207** | **209** | 212 | 299 | **ARC** | No |
| **208** | **203** | **203** | 294 | **ARC, LRU** | Yes |
| **209** | **213** | 215 | 309 | **ARC** | No |
| **210** | 180 | **178** | 271 | **LRU** | No |

#### Aggregate Statistics Across Seeds

| Algorithm | Mean Faults | Std Dev | Min Faults | Max Faults | Mean Hit % | Total Wins | Sole Wins | Tied Wins |
| :---: | :---: | :---: | :---: | :---: | :---: | :---: | :---: | :---: |
| **ARC** | **200.3** | **14.87** | 173 | **217** | **79.97%** | **8** | **6** | 2 |
| **LRU** | 202.9 | 18.33 | **170** | 226 | 79.71% | 4 | 2 | 2 |
| **FIFO** | 295.5 | 19.35 | 255 | 315 | 70.45% | 0 | 0 | 0 |

**Findings**:
- **Empirical Measured Results**:
  - Across the 10 seeded trials, ARC achieved the lowest mean page faults (**$200.3$**, std dev $14.87$) and highest mean hit ratio (**$79.97\%$**).
  - ARC won **8 out of 10 trials** (6 sole wins, 2 tied wins with LRU).
  - LRU recorded competitive results with a mean of **$202.9$ faults** (std dev $18.33$, mean hit ratio $79.71\%$), winning **4 out of 10 trials** (2 sole wins, 2 tied wins with ARC).
  - FIFO exhibited substantially higher page faults across all seeds, averaging **$295.5$ faults** (std dev $19.35$, mean hit ratio $70.45\%$, an increase of $47.5\%$ in faults over ARC) and winning **0 trials**.
- **Theoretical Interpretation and Context**:
  - *Distinction between measurement and interpretation*: While the benchmark measurements objectively demonstrate ARC's lower page-fault counts and higher win rate in this workload, attributing this performance difference to specific internal list mechanics represents an architectural interpretation rather than a directly isolated causal proof.
  - *Consistency with ARC's design*: A plausible interpretation consistent with ARC's frequency-based adaptation and $T_2$ protection is that its dual-list structure ($T_1$ for recency, $T_2$ for frequency) and adaptive target parameter $p$ provide a mechanism to shield frequently accessed pages from eviction. As pages in the hot set receive repeated references, they qualify for promotion into $T_2$. When cold pages arrive intermittently, evictions directed toward $T_1$ (subject to $p$) can avoid evicting hot pages resident in $T_2$.
  - *Contrast with FIFO*: In contrast, FIFO evicts strictly by arrival sequence without regard to access frequency, naturally evicting hot pages whenever bursts of cold references arrive.
  - *Comparison with LRU*: LRU also retains frequently accessed pages effectively if they are rereferenced before reaching the eviction tail, which is consistent with LRU's close competitive performance ($202.9$ vs $200.3$ mean faults). However, ARC's ghost cache history ($B_1, B_2$) enables dynamic rebalancing of recency versus frequency capacity, providing an adaptive heuristic that aligns well with the observed empirical advantage in this bimodal access pattern.

---

## 7. Generated Benchmark Files

The standardized benchmarking run produces two persistent CSV artifacts in `docs/experiments/`:

1. **`benchmark_results.csv`** (120 data rows):
   Contains the complete, unaggregated execution record for every single algorithmic run:
   - Columns: `workload_type,trial_id,config_desc,seed,capacity,length,algorithm,page_faults,hits,hit_ratio,miss_ratio,replacements,execution_time_ms,is_winner,is_sole_winner,is_tie`.
2. **`benchmark_summary.csv`** (66 data rows):
   Contains the structured summary dataset:
   - 30 rows for Sequential Scan (10 configurations $\times$ 3 algorithms).
   - 30 rows for Cyclic Looping (10 configurations $\times$ 3 algorithms).
   - 3 rows for Uniform Random (seed-aggregated across 10 seeds $\times$ 3 algorithms).
   - 3 rows for Repeated Locality (seed-aggregated across 10 seeds $\times$ 3 algorithms).

To regenerate both datasets and display the benchmark report:
```powershell
.\venv\Scripts\python.exe scripts/run_benchmarks.py
```
