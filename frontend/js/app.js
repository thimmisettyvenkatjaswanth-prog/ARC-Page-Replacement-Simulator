/**
 * ARC Page Replacement Simulator - Interactive Frontend Controller
 * Connects UI to FastAPI endpoints: /api/health, /api/simulate, /api/compare, /api/workload.
 * Renders live step-by-step cache lists, Chart.js benchmarks, and tie-aware rankings.
 */

document.addEventListener('DOMContentLoaded', () => {
    // -------------------------------------------------------------
    // Application State
    // -------------------------------------------------------------
    const state = {
        backendOnline: false,
        simulationData: null,
        currentStepIndex: 0,
        isPlaying: false,
        playInterval: null,
        playbackSpeedMs: 600,
        comparisonCharts: {
            barChart: null,
            hitRatioChart: null
        }
    };

    // -------------------------------------------------------------
    // DOM Elements
    // -------------------------------------------------------------
    const elements = {
        // Status & Alerts
        connectionStatus: document.getElementById('connection-status'),
        statusText: document.getElementById('status-text'),
        toastBanner: document.getElementById('toast-banner'),
        toastMessage: document.getElementById('toast-message'),
        toastClose: document.getElementById('toast-close'),

        // Config Inputs
        algorithmSelect: document.getElementById('algorithm-select'),
        cacheCapacity: document.getElementById('cache-capacity'),
        workloadType: document.getElementById('workload-type'),
        workloadLength: document.getElementById('workload-length'),
        workloadPagesCount: document.getElementById('workload-pages-count'),
        hotPagesCount: document.getElementById('hot-pages-count'),
        hotProb: document.getElementById('hot-prob'),
        loopSize: document.getElementById('loop-size'),
        referenceStringInput: document.getElementById('reference-string-input'),
        btnGenerateWorkload: document.getElementById('btn-generate-workload'),
        btnStartSimulation: document.getElementById('btn-start-simulation'),

        // Dynamic Parameter Containers
        localityParams: document.getElementById('locality-params'),
        loopingParams: document.getElementById('looping-params'),
        groupNumPages: document.getElementById('group-num-pages'),

        // Visualizer Controls
        btnStepPrev: document.getElementById('btn-step-prev'),
        btnPlayPause: document.getElementById('btn-play-pause'),
        btnStepNext: document.getElementById('btn-step-next'),
        btnReset: document.getElementById('btn-reset'),
        speedSlider: document.getElementById('speed-slider'),
        speedLabel: document.getElementById('speed-label'),

        // Visualizer Headers & Banners
        visualizerHeading: document.getElementById('visualizer-heading'),
        currentAlgorithmBadge: document.getElementById('current-algorithm-badge'),
        stepNumberBadge: document.getElementById('step-number-badge'),
        currentPageDisplay: document.getElementById('current-page-display'),
        stepOutcomePill: document.getElementById('step-outcome-pill'),
        stepMovementDesc: document.getElementById('step-movement-desc'),

        // ARC Parameter p Elements
        arcParameterContainer: document.getElementById('arc-parameter-container'),
        pDisplayVal: document.getElementById('p-display-val'),
        pTargetInt: document.getElementById('p-target-int'),
        pCapacity: document.getElementById('p-capacity'),
        pTrackFill: document.getElementById('p-track-fill'),
        pTrackMarker: document.getElementById('p-track-marker'),
        markerPVal: document.getElementById('marker-p-val'),

        // Memory List Cards (ARC)
        memoryListsContainer: document.getElementById('memory-lists-container'),
        listT1: document.getElementById('list-t1'),
        listT2: document.getElementById('list-t2'),
        listB1: document.getElementById('list-b1'),
        listB2: document.getElementById('list-b2'),
        countT1: document.getElementById('count-t1'),
        countT2: document.getElementById('count-t2'),
        countB1: document.getElementById('count-b1'),
        countB2: document.getElementById('count-b2'),

        // Single Cache (LRU / FIFO)
        singleCacheContainer: document.getElementById('single-cache-container'),
        singleCacheTitle: document.getElementById('single-cache-title'),
        singleCacheSubtitle: document.getElementById('single-cache-subtitle'),
        countSingleCache: document.getElementById('count-single-cache'),
        singleCacheLeftLabel: document.getElementById('single-cache-left-label'),
        singleCacheRightLabel: document.getElementById('single-cache-right-label'),
        listSingleCache: document.getElementById('list-single-cache'),

        // Metrics
        statRequests: document.getElementById('stat-requests'),
        statHits: document.getElementById('stat-hits'),
        statHitRatio: document.getElementById('stat-hit-ratio'),
        statMisses: document.getElementById('stat-misses'),
        statMissRatio: document.getElementById('stat-miss-ratio'),
        statReplacements: document.getElementById('stat-replacements'),
        progressPercent: document.getElementById('progress-percent'),
        ratioBarHits: document.getElementById('ratio-bar-hits'),
        ratioBarMisses: document.getElementById('ratio-bar-misses'),
        statExecTime: document.getElementById('stat-exec-time'),

        // Comparison Section
        btnRunComparison: document.getElementById('btn-run-comparison'),
        comparisonWinnerBanner: document.getElementById('comparison-winner-banner'),
        winnerHeadline: document.getElementById('winner-headline'),
        winnerDetail: document.getElementById('winner-detail'),
        winnerBadgeIcon: document.getElementById('winner-badge-icon'),

        cardCompArc: document.getElementById('card-comp-arc'),
        cardCompLru: document.getElementById('card-comp-lru'),
        cardCompFifo: document.getElementById('card-comp-fifo'),

        compArcFaults: document.getElementById('comp-arc-faults'),
        compArcHitRatio: document.getElementById('comp-arc-hit-ratio'),
        compArcHits: document.getElementById('comp-arc-hits'),
        compArcRepl: document.getElementById('comp-arc-repl'),
        compArcTime: document.getElementById('comp-arc-time'),

        compLruFaults: document.getElementById('comp-lru-faults'),
        compLruHitRatio: document.getElementById('comp-lru-hit-ratio'),
        compLruHits: document.getElementById('comp-lru-hits'),
        compLruRepl: document.getElementById('comp-lru-repl'),
        compLruTime: document.getElementById('comp-lru-time'),

        compFifoFaults: document.getElementById('comp-fifo-faults'),
        compFifoHitRatio: document.getElementById('comp-fifo-hit-ratio'),
        compFifoHits: document.getElementById('comp-fifo-hits'),
        compFifoRepl: document.getElementById('comp-fifo-repl'),
        compFifoTime: document.getElementById('comp-fifo-time'),

        // Canvases
        comparisonBarChart: document.getElementById('comparisonBarChart'),
        hitRatioBarChart: document.getElementById('hitRatioBarChart')
    };

    // -------------------------------------------------------------
    // Helper & Utility Functions
    // -------------------------------------------------------------
    function showToast(message, type = 'error') {
        elements.toastMessage.textContent = message;
        elements.toastBanner.className = `toast-banner ${type}`;
        elements.toastBanner.classList.remove('hidden');
        if (type !== 'error') {
            setTimeout(() => {
                elements.toastBanner.classList.add('hidden');
            }, 5000);
        }
    }

    elements.toastClose.addEventListener('click', () => {
        elements.toastBanner.classList.add('hidden');
    });

    function parseReferenceString(rawStr) {
        if (!rawStr || typeof rawStr !== 'string') return [];
        return rawStr
            .replace(/,/g, ' ')
            .trim()
            .split(/\s+/)
            .filter(token => token.length > 0)
            .map(token => {
                const num = Number(token);
                return Number.isInteger(num) ? num : token;
            });
    }

    // -------------------------------------------------------------
    // Health Check Verification
    // -------------------------------------------------------------
    async function checkHealth() {
        try {
            const res = await fetch('/api/health');
            if (!res.ok) throw new Error(`HTTP ${res.status}`);
            const data = await res.json();
            if (data.status === 'healthy') {
                state.backendOnline = true;
                elements.connectionStatus.className = 'status-indicator online';
                elements.statusText.textContent = `Backend Ready (v${data.version})`;
            }
        } catch (err) {
            state.backendOnline = false;
            elements.connectionStatus.className = 'status-indicator offline';
            elements.statusText.textContent = 'Backend Offline';
            console.warn('Backend connection failed:', err);
        }
    }
    checkHealth();

    // -------------------------------------------------------------
    // Algorithm Switcher UI Updates
    // -------------------------------------------------------------
    elements.algorithmSelect.addEventListener('change', () => {
        const algo = elements.algorithmSelect.value;
        const capacity = parseInt(elements.cacheCapacity.value, 10) || 4;
        elements.visualizerHeading.textContent = `${algo} Cache Memory State`;
        elements.currentAlgorithmBadge.textContent = `Algorithm: ${algo} (c = ${capacity})`;

        if (algo === 'ARC') {
            elements.arcParameterContainer.classList.remove('hidden');
            elements.memoryListsContainer.classList.remove('hidden');
            elements.singleCacheContainer.classList.add('hidden');
        } else {
            elements.arcParameterContainer.classList.add('hidden');
            elements.memoryListsContainer.classList.add('hidden');
            elements.singleCacheContainer.classList.remove('hidden');

            if (algo === 'LRU') {
                elements.singleCacheTitle.textContent = 'LRU Cache Pages';
                elements.singleCacheSubtitle.textContent = 'Resident cache ordered from MRU to LRU';
                elements.singleCacheLeftLabel.textContent = '[Most Recently Used (MRU)]';
                elements.singleCacheRightLabel.textContent = '[Least Recently Used (LRU)]';
            } else {
                elements.singleCacheTitle.textContent = 'FIFO Queue Pages';
                elements.singleCacheSubtitle.textContent = 'Resident cache ordered from Newest to Oldest';
                elements.singleCacheLeftLabel.textContent = '[Newest Arrival]';
                elements.singleCacheRightLabel.textContent = '[Oldest Arrival]';
            }
        }
    });

    // -------------------------------------------------------------
    // Workload Strategy Parameter Toggling
    // -------------------------------------------------------------
    function updateWorkloadParamFields() {
        const type = elements.workloadType.value;
        if (type === 'repeated_locality') {
            elements.localityParams.classList.remove('hidden');
            elements.loopingParams.classList.add('hidden');
            elements.groupNumPages.classList.remove('hidden');
        } else if (type === 'looping') {
            elements.localityParams.classList.add('hidden');
            elements.loopingParams.classList.remove('hidden');
            elements.groupNumPages.classList.add('hidden');
        } else if (type === 'custom') {
            elements.localityParams.classList.add('hidden');
            elements.loopingParams.classList.add('hidden');
            elements.groupNumPages.classList.add('hidden');
        } else {
            elements.localityParams.classList.add('hidden');
            elements.loopingParams.classList.add('hidden');
            elements.groupNumPages.classList.remove('hidden');
        }
    }

    elements.workloadType.addEventListener('change', updateWorkloadParamFields);
    updateWorkloadParamFields();

    // -------------------------------------------------------------
    // Generate Workload (POST /api/workload)
    // -------------------------------------------------------------
    elements.btnGenerateWorkload.addEventListener('click', async () => {
        const type = elements.workloadType.value;
        const length = parseInt(elements.workloadLength.value, 10);
        const numPages = parseInt(elements.workloadPagesCount.value, 10);
        const hotPages = parseInt(elements.hotPagesCount.value, 10);
        const hotProb = parseFloat(elements.hotProb.value);
        const loopSize = parseInt(elements.loopSize.value, 10);

        if (isNaN(length) || length < 1) {
            showToast('Workload length must be a positive integer >= 1');
            return;
        }

        const payload = {
            workload_type: type,
            length: length
        };

        if (type === 'sequential' || type === 'random') {
            payload.num_pages = numPages || 8;
        } else if (type === 'repeated_locality') {
            payload.num_pages = numPages || 8;
            payload.hot_pages_count = hotPages || 2;
            payload.hot_prob = isNaN(hotProb) ? 0.8 : hotProb;
        } else if (type === 'looping') {
            payload.loop_size = loopSize || 5;
        } else if (type === 'custom') {
            const currentPages = parseReferenceString(elements.referenceStringInput.value);
            if (currentPages.length === 0) {
                showToast('Please enter an initial custom sequence to repeat/sample.');
                return;
            }
            payload.pages = currentPages;
        }

        try {
            elements.btnGenerateWorkload.disabled = true;
            elements.btnGenerateWorkload.textContent = 'Generating...';

            const res = await fetch('/api/workload', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify(payload)
            });

            if (!res.ok) {
                const errData = await res.json().catch(() => ({}));
                throw new Error(errData.detail || `Server returned ${res.status}`);
            }

            const data = await res.json();
            elements.referenceStringInput.value = data.reference_string.join(', ');
            showToast(`Generated ${data.workload_type} workload (${data.length} references).`, 'success');
        } catch (err) {
            showToast(`Workload generation failed: ${err.message}`);
        } finally {
            elements.btnGenerateWorkload.disabled = false;
            elements.btnGenerateWorkload.innerHTML = '&#9881; Generate Workload';
        }
    });

    // -------------------------------------------------------------
    // Speed Slider Handling
    // -------------------------------------------------------------
    const speedMap = {
        1: { delay: 1200, label: '0.5x' },
        2: { delay: 800,  label: '0.8x' },
        3: { delay: 500,  label: '1x' },
        4: { delay: 250,  label: '2x' },
        5: { delay: 100,  label: '5x' }
    };

    elements.speedSlider.addEventListener('input', (e) => {
        const val = parseInt(e.target.value, 10);
        const config = speedMap[val] || speedMap[3];
        state.playbackSpeedMs = config.delay;
        elements.speedLabel.textContent = config.label;

        // Restart timer if running
        if (state.isPlaying) {
            clearInterval(state.playInterval);
            state.playInterval = setInterval(handleStepNext, state.playbackSpeedMs);
        }
    });

    // -------------------------------------------------------------
    // Start Simulation (POST /api/simulate)
    // -------------------------------------------------------------
    elements.btnStartSimulation.addEventListener('click', async () => {
        stopPlayback();

        const algorithm = elements.algorithmSelect.value;
        const capacity = parseInt(elements.cacheCapacity.value, 10);
        const refString = parseReferenceString(elements.referenceStringInput.value);

        if (isNaN(capacity) || capacity < 1) {
            showToast('Cache capacity must be an integer >= 1');
            elements.cacheCapacity.focus();
            return;
        }

        if (refString.length === 0) {
            showToast('Reference string cannot be empty. Please enter or generate page references.');
            elements.referenceStringInput.focus();
            return;
        }

        const payload = {
            algorithm: algorithm,
            capacity: capacity,
            reference_string: refString
        };

        try {
            elements.btnStartSimulation.disabled = true;
            elements.btnStartSimulation.textContent = 'Simulating...';

            const res = await fetch('/api/simulate', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify(payload)
            });

            if (!res.ok) {
                const errData = await res.json().catch(() => ({}));
                throw new Error(errData.detail || `Server returned ${res.status}`);
            }

            const data = await res.json();
            state.simulationData = data;
            state.currentStepIndex = 0;

            // Update UI headers & layout based on chosen algorithm
            elements.visualizerHeading.textContent = `${algorithm} Cache Memory State`;
            elements.currentAlgorithmBadge.textContent = `Algorithm: ${algorithm} (c = ${capacity})`;
            elements.pCapacity.textContent = capacity;

            if (algorithm === 'ARC') {
                elements.arcParameterContainer.classList.remove('hidden');
                elements.memoryListsContainer.classList.remove('hidden');
                elements.singleCacheContainer.classList.add('hidden');
            } else {
                elements.arcParameterContainer.classList.add('hidden');
                elements.memoryListsContainer.classList.add('hidden');
                elements.singleCacheContainer.classList.remove('hidden');

                if (algorithm === 'LRU') {
                    elements.singleCacheTitle.textContent = 'LRU Cache Pages';
                    elements.singleCacheSubtitle.textContent = 'Resident cache ordered from MRU to LRU';
                    elements.singleCacheLeftLabel.textContent = '[Most Recently Used (MRU)]';
                    elements.singleCacheRightLabel.textContent = '[Least Recently Used (LRU)]';
                } else {
                    elements.singleCacheTitle.textContent = 'FIFO Queue Pages';
                    elements.singleCacheSubtitle.textContent = 'Resident cache ordered from Newest to Oldest';
                    elements.singleCacheLeftLabel.textContent = '[Newest Arrival]';
                    elements.singleCacheRightLabel.textContent = '[Oldest Arrival]';
                }
            }

            // Enable playback controls
            enablePlaybackControls(true);

            // Render initial step 0
            renderStep(0);
            showToast(`Simulation ready! Loaded ${data.steps.length} reference steps.`, 'success');

            // Trigger comparison in background to keep comparison sync'd
            runComparisonBenchmark();
        } catch (err) {
            showToast(`Simulation failed: ${err.message}`);
        } finally {
            elements.btnStartSimulation.disabled = false;
            elements.btnStartSimulation.innerHTML = '&#9654; Start Simulation';
        }
    });

    function enablePlaybackControls(enabled) {
        elements.btnStepPrev.disabled = !enabled;
        elements.btnPlayPause.disabled = !enabled;
        elements.btnStepNext.disabled = !enabled;
        elements.btnReset.disabled = !enabled;
    }

    // -------------------------------------------------------------
    // Step-by-Step Rendering Engine
    // -------------------------------------------------------------
    function renderStep(stepIndex) {
        if (!state.simulationData) return;

        const totalSteps = state.simulationData.steps.length;
        const capacity = state.simulationData.capacity;
        const algorithm = state.simulationData.algorithm;

        // Step 0: Before first request
        if (stepIndex === 0) {
            elements.stepNumberBadge.textContent = `Step 0 / ${totalSteps}`;
            elements.currentPageDisplay.textContent = '-';
            elements.currentPageDisplay.classList.remove('highlight');
            elements.stepOutcomePill.className = 'status-pill status-ready';
            elements.stepOutcomePill.textContent = 'READY';
            elements.stepMovementDesc.textContent = 'Initial empty cache state. Step or play to start.';

            // Reset ARC Parameter p
            elements.pDisplayVal.textContent = '0.00';
            elements.pTargetInt.textContent = '0';
            elements.pTrackFill.style.width = '0%';
            elements.pTrackMarker.style.left = '0%';
            elements.markerPVal.textContent = '0';

            // Clear Lists
            renderPageList(elements.listT1, elements.countT1, []);
            renderPageList(elements.listT2, elements.countT2, []);
            renderPageList(elements.listB1, elements.countB1, [], true);
            renderPageList(elements.listB2, elements.countB2, [], true);
            renderSingleCacheList([]);

            // Reset Metrics
            updateMetricsDisplay(0, 0, 0, 0, 0.0, 0.0, 0.0);
            return;
        }

        const step = state.simulationData.steps[stepIndex - 1];

        // Banner Updates
        elements.stepNumberBadge.textContent = `Step ${step.step} / ${totalSteps}`;
        elements.currentPageDisplay.textContent = step.page;
        elements.currentPageDisplay.classList.add('highlight');

        // Status Outcome Pill
        if (step.hit) {
            elements.stepOutcomePill.className = 'status-pill status-hit';
            elements.stepOutcomePill.textContent = 'CACHE HIT';
        } else if (step.hit_type === 'GHOST_B1') {
            elements.stepOutcomePill.className = 'status-pill status-ghost';
            elements.stepOutcomePill.textContent = 'GHOST HIT (B1)';
        } else if (step.hit_type === 'GHOST_B2') {
            elements.stepOutcomePill.className = 'status-pill status-ghost';
            elements.stepOutcomePill.textContent = 'GHOST HIT (B2)';
        } else {
            elements.stepOutcomePill.className = 'status-pill status-miss';
            elements.stepOutcomePill.textContent = 'PAGE FAULT (MISS)';
        }

        // Movement description
        let movementText = step.movement || '';
        if (step.evicted_page !== null && step.evicted_page !== undefined) {
            movementText += ` [Evicted page ${step.evicted_page}]`;
        }
        elements.stepMovementDesc.textContent = movementText || 'Processed page access.';

        // ARC Memory Lists & Parameter p
        if (algorithm === 'ARC') {
            const pVal = typeof step.p === 'number' ? step.p : 0;
            elements.pDisplayVal.textContent = pVal.toFixed(2);
            elements.pTargetInt.textContent = Math.floor(pVal);
            const pPercent = Math.min(100, Math.max(0, (pVal / capacity) * 100));
            elements.pTrackFill.style.width = `${pPercent}%`;
            elements.pTrackMarker.style.left = `${pPercent}%`;
            elements.markerPVal.textContent = pVal.toFixed(1);

            renderPageList(elements.listT1, elements.countT1, step.T1 || [], false, step.page, step.hit);
            renderPageList(elements.listT2, elements.countT2, step.T2 || [], false, step.page, step.hit);
            renderPageList(elements.listB1, elements.countB1, step.B1 || [], true);
            renderPageList(elements.listB2, elements.countB2, step.B2 || [], true);
        } else {
            // LRU or FIFO
            renderSingleCacheList(step.pages || [], step.page, step.hit);
        }

        // Cumulative Metrics
        const execTime = state.simulationData.metrics ? state.simulationData.metrics.execution_time_ms : 0;
        const currentReplacements = (step.state && typeof step.state.replacements === 'number')
            ? step.state.replacements
            : 0;

        updateMetricsDisplay(
            step.step,
            step.cumulative_hits,
            step.cumulative_page_faults,
            currentReplacements,
            step.cumulative_hit_ratio,
            step.cumulative_miss_ratio,
            execTime
        );
    }

    function renderPageList(container, countBadge, pages, isGhost = false, activePage = null, isHit = false) {
        countBadge.textContent = pages.length;
        container.innerHTML = '';

        if (!pages || pages.length === 0) {
            container.innerHTML = '<div class="empty-list-placeholder">Empty</div>';
            return;
        }

        pages.forEach((page, idx) => {
            const item = document.createElement('div');
            item.className = `page-item ${isGhost ? 'ghost-item' : ''}`;
            if (!isGhost && isHit && page === activePage) {
                item.classList.add('just-accessed');
            }

            const labelSpan = document.createElement('span');
            labelSpan.textContent = `Page ${page}`;

            const posTag = document.createElement('span');
            posTag.className = 'field-hint';
            if (idx === 0) {
                posTag.textContent = isGhost ? 'Recent' : 'MRU';
            } else if (idx === pages.length - 1) {
                posTag.textContent = isGhost ? 'Oldest' : 'LRU';
            }

            item.appendChild(labelSpan);
            item.appendChild(posTag);
            container.appendChild(item);
        });
    }

    function renderSingleCacheList(pages, activePage = null, isHit = false) {
        elements.countSingleCache.textContent = `${pages.length} / ${state.simulationData.capacity}`;
        elements.listSingleCache.innerHTML = '';

        if (!pages || pages.length === 0) {
            elements.listSingleCache.innerHTML = '<div class="empty-list-placeholder">Empty</div>';
            return;
        }

        pages.forEach((page, idx) => {
            const item = document.createElement('div');
            item.className = 'page-item';
            if (isHit && page === activePage) {
                item.classList.add('just-accessed');
            }

            const labelSpan = document.createElement('span');
            labelSpan.textContent = `Page ${page}`;

            const posTag = document.createElement('span');
            posTag.className = 'field-hint';
            if (idx === 0) {
                posTag.textContent = elements.singleCacheLeftLabel.textContent.replace(/[\[\]]/g, '');
            } else if (idx === pages.length - 1) {
                posTag.textContent = elements.singleCacheRightLabel.textContent.replace(/[\[\]]/g, '');
            }

            item.appendChild(labelSpan);
            if (posTag.textContent) {
                item.appendChild(posTag);
            }
            elements.listSingleCache.appendChild(item);
        });
    }

    function updateMetricsDisplay(requests, hits, misses, replacements, hitRatio, missRatio, execTime) {
        elements.statRequests.textContent = requests;
        elements.statHits.textContent = hits;
        elements.statMisses.textContent = misses;
        elements.statReplacements.textContent = replacements;

        const hitRatioPct = (hitRatio * 100).toFixed(2);
        const missRatioPct = (missRatio * 100).toFixed(2);

        elements.statHitRatio.textContent = `Hit Ratio: ${hitRatioPct}%`;
        elements.statMissRatio.textContent = `Fault Rate: ${missRatioPct}%`;

        elements.progressPercent.textContent = `${hitRatioPct}% Hits (${missRatioPct}% Faults)`;
        elements.ratioBarHits.style.width = `${hitRatioPct}%`;
        elements.ratioBarMisses.style.width = `${missRatioPct}%`;

        elements.statExecTime.textContent = `${execTime.toFixed(3)} ms`;
    }

    // -------------------------------------------------------------
    // Playback Navigation Handlers
    // -------------------------------------------------------------
    function handleStepNext() {
        if (!state.simulationData) return;
        const total = state.simulationData.steps.length;
        if (state.currentStepIndex < total) {
            state.currentStepIndex++;
            renderStep(state.currentStepIndex);
        } else {
            stopPlayback();
        }
    }

    function handleStepPrev() {
        if (!state.simulationData) return;
        if (state.currentStepIndex > 0) {
            state.currentStepIndex--;
            renderStep(state.currentStepIndex);
        }
    }

    function togglePlayPause() {
        if (!state.simulationData) return;

        if (state.isPlaying) {
            stopPlayback();
        } else {
            // If already at end, restart from step 0
            if (state.currentStepIndex >= state.simulationData.steps.length) {
                state.currentStepIndex = 0;
                renderStep(0);
            }
            state.isPlaying = true;
            elements.btnPlayPause.textContent = '⏸ Pause';
            elements.btnPlayPause.classList.add('paused');
            state.playInterval = setInterval(handleStepNext, state.playbackSpeedMs);
        }
    }

    function stopPlayback() {
        if (state.isPlaying) {
            state.isPlaying = false;
            clearInterval(state.playInterval);
            elements.btnPlayPause.innerHTML = '&#9654; Play';
            elements.btnPlayPause.classList.remove('paused');
        }
    }

    elements.btnStepNext.addEventListener('click', () => {
        stopPlayback();
        handleStepNext();
    });

    elements.btnStepPrev.addEventListener('click', () => {
        stopPlayback();
        handleStepPrev();
    });

    elements.btnPlayPause.addEventListener('click', togglePlayPause);

    elements.btnReset.addEventListener('click', () => {
        stopPlayback();
        state.currentStepIndex = 0;
        renderStep(0);
    });

    // -------------------------------------------------------------
    // Multi-Algorithm Comparative Benchmark (POST /api/compare)
    // -------------------------------------------------------------
    async function runComparisonBenchmark() {
        const capacity = parseInt(elements.cacheCapacity.value, 10);
        const refString = parseReferenceString(elements.referenceStringInput.value);

        if (isNaN(capacity) || capacity < 1 || refString.length === 0) return;

        try {
            elements.btnRunComparison.disabled = true;
            elements.btnRunComparison.textContent = 'Comparing...';

            const res = await fetch('/api/compare', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({
                    capacity: capacity,
                    reference_string: refString,
                    include_steps: false
                })
            });

            if (!res.ok) {
                const errData = await res.json().catch(() => ({}));
                throw new Error(errData.detail || `Server returned ${res.status}`);
            }

            const data = await res.json();
            renderComparisonResults(data);
        } catch (err) {
            console.error('Comparison error:', err);
            showToast(`Comparison error: ${err.message}`);
        } finally {
            elements.btnRunComparison.disabled = false;
            elements.btnRunComparison.innerHTML = '&#9878; Run 3-Way Comparison';
        }
    }

    elements.btnRunComparison.addEventListener('click', runComparisonBenchmark);

    function renderComparisonResults(data) {
        const results = data.results;
        const summary = data.comparison_summary;
        const winners = data.best_algorithms || [];
        const isTie = data.is_tie;

        // Reset winner card highlights
        elements.cardCompArc.classList.remove('is-winner');
        elements.cardCompLru.classList.remove('is-winner');
        elements.cardCompFifo.classList.remove('is-winner');

        // Winner Banner Display (Page-Fault Only Tie Logic)
        elements.comparisonWinnerBanner.classList.remove('hidden');
        if (isTie) {
            elements.winnerBadgeIcon.textContent = '🤝 TIE DETECTED';
            elements.winnerHeadline.textContent = `Tie for Best Performance: ${winners.join(' & ')}`;
            elements.winnerDetail.textContent = `All tied algorithms achieved the identical minimum of ${summary.fewest_page_faults} page faults (${(summary.highest_hit_ratio * 100).toFixed(2)}% hit ratio). No secondary metrics used to break ties.`;
        } else {
            const soleWinner = data.best_algorithm;
            elements.winnerBadgeIcon.textContent = '🏆 BEST PERFORMER';
            elements.winnerHeadline.textContent = `${soleWinner} Achieved Lowest Page Faults`;
            elements.winnerDetail.textContent = summary.summary_text;
        }

        // Highlight winner cards
        winners.forEach(winner => {
            if (winner === 'ARC') elements.cardCompArc.classList.add('is-winner');
            if (winner === 'LRU') elements.cardCompLru.classList.add('is-winner');
            if (winner === 'FIFO') elements.cardCompFifo.classList.add('is-winner');
        });

        // Fill ARC Card
        if (results.ARC) {
            elements.compArcFaults.textContent = results.ARC.page_faults;
            elements.compArcHitRatio.textContent = `${(results.ARC.hit_ratio * 100).toFixed(2)}%`;
            elements.compArcHits.textContent = results.ARC.hits;
            elements.compArcRepl.textContent = results.ARC.replacements;
            elements.compArcTime.textContent = `${results.ARC.execution_time_ms.toFixed(3)} ms`;
        }

        // Fill LRU Card
        if (results.LRU) {
            elements.compLruFaults.textContent = results.LRU.page_faults;
            elements.compLruHitRatio.textContent = `${(results.LRU.hit_ratio * 100).toFixed(2)}%`;
            elements.compLruHits.textContent = results.LRU.hits;
            elements.compLruRepl.textContent = results.LRU.replacements;
            elements.compLruTime.textContent = `${results.LRU.execution_time_ms.toFixed(3)} ms`;
        }

        // Fill FIFO Card
        if (results.FIFO) {
            elements.compFifoFaults.textContent = results.FIFO.page_faults;
            elements.compFifoHitRatio.textContent = `${(results.FIFO.hit_ratio * 100).toFixed(2)}%`;
            elements.compFifoHits.textContent = results.FIFO.hits;
            elements.compFifoRepl.textContent = results.FIFO.replacements;
            elements.compFifoTime.textContent = `${results.FIFO.execution_time_ms.toFixed(3)} ms`;
        }

        // Render Charts with Chart.js
        renderCharts(results);
    }

    // -------------------------------------------------------------
    // Chart.js Rendering
    // -------------------------------------------------------------
    function renderCharts(results) {
        if (typeof Chart === 'undefined') {
            console.warn('Chart.js not loaded.');
            return;
        }

        const labels = ['ARC', 'LRU', 'FIFO'];
        const pageFaults = [results.ARC?.page_faults || 0, results.LRU?.page_faults || 0, results.FIFO?.page_faults || 0];
        const hits = [results.ARC?.hits || 0, results.LRU?.hits || 0, results.FIFO?.hits || 0];
        const hitRatios = [
            Number(((results.ARC?.hit_ratio || 0) * 100).toFixed(2)),
            Number(((results.LRU?.hit_ratio || 0) * 100).toFixed(2)),
            Number(((results.FIFO?.hit_ratio || 0) * 100).toFixed(2))
        ];

        // 1. Bar Chart: Page Faults vs Hits
        if (state.comparisonCharts.barChart) {
            state.comparisonCharts.barChart.destroy();
        }

        const ctx1 = elements.comparisonBarChart.getContext('2d');
        state.comparisonCharts.barChart = new Chart(ctx1, {
            type: 'bar',
            data: {
                labels: labels,
                datasets: [
                    {
                        label: 'Page Faults (Lower is Better)',
                        data: pageFaults,
                        backgroundColor: 'rgba(244, 63, 94, 0.75)',
                        borderColor: '#f43f5e',
                        borderWidth: 1.5,
                        borderRadius: 4
                    },
                    {
                        label: 'Hits (Higher is Better)',
                        data: hits,
                        backgroundColor: 'rgba(16, 185, 129, 0.75)',
                        borderColor: '#10b981',
                        borderWidth: 1.5,
                        borderRadius: 4
                    }
                ]
            },
            options: {
                responsive: true,
                maintainAspectRatio: false,
                animation: { duration: 400 },
                plugins: {
                    legend: {
                        labels: { color: '#94a3b8', font: { family: 'Plus Jakarta Sans', size: 11 } }
                    },
                    tooltip: {
                        backgroundColor: 'rgba(10, 14, 23, 0.95)',
                        borderColor: 'rgba(255, 255, 255, 0.1)',
                        borderWidth: 1
                    }
                },
                scales: {
                    x: {
                        ticks: { color: '#94a3b8', font: { family: 'JetBrains Mono', weight: 'bold' } },
                        grid: { color: 'rgba(255, 255, 255, 0.04)' }
                    },
                    y: {
                        beginAtZero: true,
                        ticks: { color: '#94a3b8', stepSize: 1, font: { family: 'JetBrains Mono' } },
                        grid: { color: 'rgba(255, 255, 255, 0.05)' }
                    }
                }
            }
        });

        // 2. Bar Chart: Hit Ratio (%)
        if (state.comparisonCharts.hitRatioChart) {
            state.comparisonCharts.hitRatioChart.destroy();
        }

        const ctx2 = elements.hitRatioBarChart.getContext('2d');
        state.comparisonCharts.hitRatioChart = new Chart(ctx2, {
            type: 'bar',
            data: {
                labels: labels,
                datasets: [{
                    label: 'Hit Ratio (%)',
                    data: hitRatios,
                    backgroundColor: [
                        'rgba(56, 189, 248, 0.8)',
                        'rgba(245, 158, 11, 0.8)',
                        'rgba(168, 85, 247, 0.8)'
                    ],
                    borderColor: [
                        '#38bdf8',
                        '#f59e0b',
                        '#a855f7'
                    ],
                    borderWidth: 1.5,
                    borderRadius: 4
                }]
            },
            options: {
                responsive: true,
                maintainAspectRatio: false,
                animation: { duration: 400 },
                plugins: {
                    legend: { display: false },
                    tooltip: {
                        callbacks: {
                            label: (ctx) => `Hit Ratio: ${ctx.parsed.y}%`
                        }
                    }
                },
                scales: {
                    x: {
                        ticks: { color: '#94a3b8', font: { family: 'JetBrains Mono', weight: 'bold' } },
                        grid: { color: 'rgba(255, 255, 255, 0.04)' }
                    },
                    y: {
                        beginAtZero: true,
                        max: 100,
                        ticks: {
                            color: '#94a3b8',
                            callback: (val) => `${val}%`,
                            font: { family: 'JetBrains Mono' }
                        },
                        grid: { color: 'rgba(255, 255, 255, 0.05)' }
                    }
                }
            }
        });
    }

    // Initialize initial benchmark on page load
    runComparisonBenchmark();
});
