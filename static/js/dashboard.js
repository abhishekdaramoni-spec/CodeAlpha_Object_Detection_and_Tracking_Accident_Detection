document.addEventListener("DOMContentLoaded", function () {
    // 1. Theme Configuration Logic (Persistent Light/Dark Mode)
    const themeToggleBtn = document.getElementById("theme-toggle-btn");
    const themeIcon = document.getElementById("theme-toggle-icon");
    
    let activeTheme = localStorage.getItem("theme") || "dark";
    document.documentElement.setAttribute("data-theme", activeTheme);
    updateThemeIcon(activeTheme);
    
    if (themeToggleBtn) {
        themeToggleBtn.addEventListener("click", function () {
            activeTheme = document.documentElement.getAttribute("data-theme") === "dark" ? "light" : "dark";
            document.documentElement.setAttribute("data-theme", activeTheme);
            localStorage.setItem("theme", activeTheme);
            updateThemeIcon(activeTheme);
            
            // Re-render charts on current viewport to match background grid colors
            if (window.renderCharts) {
                window.renderCharts();
            }
        });
    }
    
    function updateThemeIcon(theme) {
        if (!themeIcon) return;
        if (theme === "dark") {
            themeIcon.className = "fa-solid fa-sun text-warning";
        } else {
            themeIcon.className = "fa-solid fa-moon text-primary";
        }
    }

    // 2. Viewport Page Detections
    const isDashboard = document.getElementById("dashboard-viewport") !== null;
    const isLive = document.getElementById("live-viewport") !== null;
    const isHistory = document.getElementById("history-viewport") !== null;
    const isSettings = document.getElementById("settings-viewport") !== null;
    const isAnalytics = document.getElementById("analytics-viewport") !== null;
    const isGallery = document.getElementById("gallery-viewport") !== null;

    // Mobile Sidebar Toggle
    const sidebarToggle = document.getElementById("sidebar-toggle");
    const appSidebar = document.querySelector(".app-sidebar");
    if (sidebarToggle && appSidebar) {
        sidebarToggle.addEventListener("click", function () {
            appSidebar.classList.toggle("show");
        });
    }

    // 3. Image Refresh Loop (Disabled - Native MJPEG stream is served directly from /video_feed)
    // const videoStream = document.getElementById("video-stream");
    // if (videoStream) {
    //     setInterval(() => {
    //         videoStream.src = "/static/uploads/live_frame.jpg?t=" + Date.now();
    //     }, 100);
    // }

    // 4. Incident Resolution Handler (Global hook)
    window.resolveIncident = function (incidentId, button) {
        if (!confirm("Confirm marking this incident alert as Resolved?")) {
            return;
        }

        fetch(`/resolve/${incidentId}`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' }
        })
        .then(response => response.json())
        .then(data => {
            if (data.status === 'success') {
                if (button) {
                    const row = button.closest("tr");
                    if (row) {
                        const badges = row.querySelectorAll(".badge-saas");
                        badges.forEach(badge => {
                            if (badge.textContent.trim() === "Active") {
                                badge.className = "badge badge-saas bg-resolved";
                                badge.textContent = "Resolved";
                            }
                        });
                        button.remove();
                    } else {
                        button.outerHTML = `<span class="badge badge-saas bg-resolved">Resolved</span>`;
                    }
                }
                
                showToast("System Info", `Incident #${incidentId} resolved successfully.`, "info");
                
                if (isDashboard) {
                    fetchDashboardStats();
                }
            } else {
                alert("Failed to resolve incident: " + data.message);
            }
        })
        .catch(error => {
            console.error("Resolution connection failed:", error);
            alert("Failed to connect to the server.");
        });
    };

    // 5. Toast Notifications Spawner
    function showToast(title, message, type="info") {
        const toastContainer = document.getElementById("toast-container");
        if (!toastContainer) {
            // Create toast container if missing
            const container = document.createElement("div");
            container.id = "toast-container";
            container.className = "position-fixed top-0 end-0 p-3";
            container.style.zIndex = "1080";
            document.body.appendChild(container);
        }
        
        let headerColor = "bg-primary";
        if (type === "danger") headerColor = "bg-danger";
        else if (type === "warning") headerColor = "bg-warning text-dark";
        else if (type === "success") headerColor = "bg-success";
        
        const toastId = "toast_" + Date.now();
        const toastHtml = `
        <div id="${toastId}" class="toast show bg-dark text-white border border-secondary" role="alert" aria-live="assertive" aria-atomic="true" data-bs-delay="4000">
            <div class="toast-header ${headerColor} text-white">
                <i class="fa-solid fa-bell me-2"></i>
                <strong class="me-auto">${title}</strong>
                <small>Just now</small>
                <button type="button" class="btn-close btn-close-white" data-bs-dismiss="toast" aria-label="Close"></button>
            </div>
            <div class="toast-body">
                ${message}
            </div>
        </div>
        `;
        
        document.getElementById("toast-container").insertAdjacentHTML('beforeend', toastHtml);
        
        const toastEl = document.getElementById(toastId);
        setTimeout(() => {
            if (toastEl) {
                toastEl.classList.remove("show");
                setTimeout(() => toastEl.remove(), 500);
            }
        }, 4000);
    }

    // 6. Real-time Dashboard Polling Loop
    let lastCheckedIncidentId = null;
    
    if (isDashboard || isLive) {
        fetchDashboardStats();
        setInterval(fetchDashboardStats, 3000);
    }
    
    function fetchDashboardStats() {
        fetch('/api/stats')
            .then(res => res.json())
            .then(data => {
                // State transitions page reloads
                const videoStreamEl = document.getElementById("video-stream");
                const isStreamVisible = (videoStreamEl !== null);
                const isBackendProcessing = (data.system_status === "PROCESSING");

                if (isBackendProcessing && !isStreamVisible) {
                    location.reload();
                    return;
                } else if (!isBackendProcessing && isStreamVisible) {
                    location.reload();
                    return;
                }

                // Update system metrics
                const statusCard = document.getElementById("stat-status");
                if (statusCard) {
                    statusCard.textContent = data.system_status;
                    statusCard.className = data.system_status === "PROCESSING" ? "stat-value text-rose saas-blink" : "stat-value text-green";
                }
                
                updateTextElement("stat-total", data.total_detections);
                updateTextElement("stat-active-tracks", data.active_tracks);
                updateTextElement("stat-images-count", data.images_processed);
                updateTextElement("stat-videos-count", data.videos_processed);
                updateTextElement("stat-avg-confidence", (data.avg_confidence * 100).toFixed(0) + "%");
                
                // Host system metrics (CPU/RAM)
                updateTextElement("stat-cpu-val", data.cpu_percent + "%");
                updateTextElement("stat-ram-val", data.memory_percent + "%");
                
                const cpuBar = document.getElementById("stat-cpu-bar");
                if (cpuBar) cpuBar.style.width = data.cpu_percent + "%";
                const ramBar = document.getElementById("stat-ram-bar");
                if (ramBar) ramBar.style.width = data.memory_percent + "%";

                // Update object counts table
                updateTextElement("count-person", data.current_counts.person || 0);
                updateTextElement("count-vehicle", data.current_counts.vehicle || 0);

                // Toast notifications for newly detected high-severity incidents
                if (data.recent_incidents && data.recent_incidents.length > 0) {
                    const newest = data.recent_incidents[0];
                    if (lastCheckedIncidentId !== null && newest.id > lastCheckedIncidentId) {
                        const toastType = newest.severity === "High" ? "danger" : (newest.severity === "Moderate" ? "warning" : "success");
                        showToast(`HIGH ALERT: ${newest.incident_type}`, newest.description, toastType);
                    }
                    lastCheckedIncidentId = newest.id;
                } else if (lastCheckedIncidentId === null) {
                    lastCheckedIncidentId = 0;
                }

                // Update screenshot widgets on dashboard
                const gallery = document.getElementById("dashboard-screenshot-gallery");
                if (gallery && data.recent_incidents) {
                    if (data.recent_incidents.length === 0) {
                        gallery.innerHTML = `<p class="text-muted text-center py-4">No screenshots captured.</p>`;
                    } else {
                        let html = "";
                        data.recent_incidents.forEach(item => {
                            html += `
                            <div class="screenshot-item" onclick="openLightbox('/${item.screenshot_path}', '${item.incident_type}')" title="${item.incident_type}">
                                <img src="/${item.screenshot_path}" alt="Snap">
                            </div>
                            `;
                        });
                        gallery.innerHTML = html;
                    }
                }
            })
            .catch(err => console.error("Error polling statistics:", err));
    }

    function updateTextElement(id, value) {
        const el = document.getElementById(id);
        if (el) el.textContent = value;
    }

    // 7. Analytics & Comprehensive Charts (Chart.js)
    if (isAnalytics || isDashboard) {
        window.renderCharts = function () {
            fetch('/api/analytics')
                .then(res => res.json())
                .then(data => {
                    renderAnalyticsCharts(data);
                })
                .catch(err => console.error("Error drawing charts:", err));
        };
        
        window.renderCharts();
    }

    let barChart = null;
    let pieChart = null;
    let lineChart = null;
    let trendChartInstance = null;

    function renderAnalyticsCharts(data) {
        const theme = document.documentElement.getAttribute("data-theme") || "dark";
        const gridColor = theme === "dark" ? "rgba(255,255,255,0.06)" : "rgba(0,0,0,0.06)";
        const labelColor = theme === "dark" ? "#94a3b8" : "#64748b";

        // Chart 1: Incidents by Category (Bar Chart)
        const barCtx = document.getElementById('classChart');
        if (barCtx) {
            if (barChart) barChart.destroy();
            const cats = data.category_distribution || {};
            barChart = new Chart(barCtx.getContext('2d'), {
                type: 'bar',
                data: {
                    labels: ['Collision', 'Sudden Stop', 'Fall Alert', 'Proximity', 'Intrusion', 'Crowd'],
                    datasets: [{
                        data: [
                            cats['Vehicle Collision'] || 0,
                            cats['Sudden Stop'] || 0,
                            cats['Pedestrian Fall'] || 0,
                            cats['Proximity Warning'] || 0,
                            cats['Intrusion Alert'] || 0,
                            cats['Crowd Alert'] || 0
                        ],
                        backgroundColor: ['#f43f5e', '#f97316', '#fbbf24', '#38bdf8', '#a855f7', '#ec4899'],
                        borderWidth: 0
                    }]
                },
                options: {
                    responsive: true,
                    maintainAspectRatio: false,
                    plugins: { legend: { display: false } },
                    scales: {
                        x: { grid: { color: gridColor }, ticks: { color: labelColor } },
                        y: { grid: { color: gridColor }, ticks: { color: labelColor, stepSize: 1, beginAtZero: true } }
                    }
                }
            });
        }

        // Chart 2: Incident Severity Distribution (Pie/Doughnut Chart)
        const pieCtx = document.getElementById('sourceChart');
        if (pieCtx) {
            if (pieChart) pieChart.destroy();
            const sevs = data.severity_distribution || { 'Low': 0, 'Moderate': 0, 'High': 0 };
            pieChart = new Chart(pieCtx.getContext('2d'), {
                type: 'pie',
                data: {
                    labels: ['Low Severity', 'Moderate Severity', 'High Severity'],
                    datasets: [{
                        data: [sevs.Low || 0, sevs.Moderate || 0, sevs.High || 0],
                        backgroundColor: ['#38bdf8', '#f97316', '#f43f5e'],
                        borderColor: theme === 'dark' ? '#111827' : '#ffffff',
                        borderWidth: 2
                    }]
                },
                options: {
                    responsive: true,
                    maintainAspectRatio: false,
                    plugins: {
                        legend: { position: 'bottom', labels: { color: labelColor } }
                    }
                }
            });
        }

        // Chart 3: Daily Activity Trend (Line Chart)
        const lineCtx = document.getElementById('trendChart');
        if (lineCtx) {
            if (lineChart) lineChart.destroy();
            lineChart = new Chart(lineCtx.getContext('2d'), {
                type: 'line',
                data: {
                    labels: data.daily_labels.length > 0 ? data.daily_labels : ['No Data'],
                    datasets: [{
                        label: 'Incidents Logged',
                        data: data.daily_values.length > 0 ? data.daily_values : [0],
                        borderColor: '#f43f5e',
                        backgroundColor: 'rgba(244,63,94,0.15)',
                        fill: true,
                        tension: 0.35,
                        borderWidth: 2,
                        pointBackgroundColor: '#f43f5e'
                    }]
                },
                options: {
                    responsive: true,
                    maintainAspectRatio: false,
                    plugins: { legend: { display: false } },
                    scales: {
                        x: { grid: { color: gridColor }, ticks: { color: labelColor } },
                        y: { grid: { color: gridColor }, ticks: { color: labelColor, stepSize: 1, beginAtZero: true } }
                    }
                }
            });
        }

        // Chart 4: Confidence Trend of Recent Incidents
        const confCtx = document.getElementById('confidenceChart');
        if (confCtx) {
            if (trendChartInstance) trendChartInstance.destroy();
            trendChartInstance = new Chart(confCtx.getContext('2d'), {
                type: 'line',
                data: {
                    labels: data.confidence_labels.length > 0 ? data.confidence_labels : ['No Data'],
                    datasets: [{
                        label: 'Average Confidence',
                        data: data.confidence_values.length > 0 ? data.confidence_values : [0.0],
                        borderColor: '#38bdf8',
                        backgroundColor: 'transparent',
                        tension: 0.2,
                        borderWidth: 2,
                        pointBackgroundColor: '#38bdf8'
                    }]
                },
                options: {
                    responsive: true,
                    maintainAspectRatio: false,
                    plugins: { legend: { display: false } },
                    scales: {
                        x: { grid: { color: gridColor }, ticks: { color: labelColor } },
                        y: { grid: { color: gridColor }, ticks: { color: labelColor, max: 1.0, min: 0.0 } }
                    }
                }
            });
        }
    }

    // 8. Settings sliders and Class Toggles
    if (isSettings) {
        const slider = document.getElementById("confidence-slider");
        const valSpan = document.getElementById("confidence-val");
        
        if (slider && valSpan) {
            slider.addEventListener("input", function () {
                valSpan.textContent = parseFloat(slider.value).toFixed(2);
            });
            
            slider.addEventListener("change", function () {
                saveSettingsState();
            });
        }
        
        // Track checkboxes toggling
        const checkboxes = document.querySelectorAll(".class-filter-checkbox");
        checkboxes.forEach(box => {
            box.addEventListener("change", function () {
                saveSettingsState();
            });
        });
        
        const exportCheckboxes = document.querySelectorAll(".export-filter-checkbox");
        exportCheckboxes.forEach(box => {
            box.addEventListener("change", function () {
                saveSettingsState();
            });
        });
        
        function saveSettingsState() {
            const conf = slider ? parseFloat(slider.value) : 0.25;
            
            // Gather active classes
            const enabledClasses = [];
            checkboxes.forEach(box => {
                if (box.checked) {
                    enabledClasses.push(box.getAttribute("data-class"));
                }
            });
            
            const enableCsv = document.getElementById("export-csv") ? document.getElementById("export-csv").checked : true;
            const enablePdf = document.getElementById("export-pdf") ? document.getElementById("export-pdf").checked : true;
            
            fetch('/api/settings', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({
                    confidence_threshold: conf,
                    enabled_classes: enabledClasses,
                    enable_csv_export: enableCsv,
                    enable_pdf_export: enablePdf
                })
            })
            .then(res => res.json())
            .then(data => {
                if (data.status === "success") {
                    showToast("Success", "Settings synchronized successfully.", "success");
                } else {
                    alert("Failed to synchronize settings: " + data.message);
                }
            })
            .catch(err => console.error("Settings POST sync failed:", err));
        }
    }

    // 9. Screenshot Lightbox modal
    const lightboxModal = document.getElementById("lightboxModal");
    const lightboxImage = document.getElementById("lightboxImage");

    window.openLightbox = function (imageSrc, incidentType) {
        if (lightboxImage) {
            currentLightboxBaseUrl = imageSrc;
            const tabs = document.getElementById("lightbox-tabs");
            const title = document.getElementById("lightboxTitle");
            
            if (title) {
                title.innerHTML = incidentType ? `<i class="fa-solid fa-camera-retro me-2 text-cyan"></i> ${incidentType} - Evidence Capture` : `<i class="fa-solid fa-camera-retro me-2 text-cyan"></i> Incident Evidence Capture`;
            }
            
            // Show tabs only for Vehicle Collision
            if (tabs) {
                if (incidentType === "Vehicle Collision" || incidentType === "Confirmed Accident") {
                    tabs.classList.remove("d-none");
                    tabs.classList.add("d-flex");
                    setActiveTab("impact");
                    lightboxImage.src = imageSrc;
                } else {
                    tabs.classList.remove("d-flex");
                    tabs.classList.add("d-none");
                    lightboxImage.src = imageSrc;
                }
            } else {
                lightboxImage.src = imageSrc;
            }
            
            const modal = new bootstrap.Modal(lightboxModal);
            modal.show();
        }
    };

    window.switchLightboxImage = function (phase) {
        if (!lightboxImage || !currentLightboxBaseUrl) return;
        
        // Parse url to get the filename before the extension
        const lastDotIndex = currentLightboxBaseUrl.lastIndexOf('.');
        if (lastDotIndex === -1) return;
        
        const base = currentLightboxBaseUrl.substring(0, lastDotIndex);
        const ext = currentLightboxBaseUrl.substring(lastDotIndex);
        
        let newSrc = currentLightboxBaseUrl;
        if (phase === 'pre') {
            newSrc = base + '_pre' + ext;
        } else if (phase === 'post') {
            newSrc = base + '_post' + ext;
        }
        
        lightboxImage.src = newSrc;
        setActiveTab(phase);
    };

    function setActiveTab(phase) {
        const btnPre = document.getElementById("btn-pre");
        const btnImpact = document.getElementById("btn-impact");
        const btnPost = document.getElementById("btn-post");
        
        if (btnPre) btnPre.classList.remove("active", "btn-info", "btn-outline-info");
        if (btnImpact) btnImpact.classList.remove("active", "btn-danger", "btn-outline-danger");
        if (btnPost) btnPost.classList.remove("active", "btn-warning", "btn-outline-warning");
        
        if (phase === 'pre' && btnPre) {
            btnPre.classList.add("active", "btn-info");
        } else if (btnPre) {
            btnPre.classList.add("btn-outline-info");
        }
        
        if (phase === 'impact' && btnImpact) {
            btnImpact.classList.add("active", "btn-danger");
        } else if (btnImpact) {
            btnImpact.classList.add("btn-outline-danger");
        }
        
        if (phase === 'post' && btnPost) {
            btnPost.classList.add("active", "btn-warning");
        } else if (btnPost) {
            btnPost.classList.add("btn-outline-warning");
        }
    }
});
