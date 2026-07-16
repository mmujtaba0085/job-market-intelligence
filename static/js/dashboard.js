// Dashboard Data Loading and Chart Rendering
let charts = {};

// Shared categorical palette for multi-series charts (geo / sources / top skills).
// Derived from the GreyWave tokens instead of a stock rainbow set, so charts stay
// visually part of the same flat, warm-neutral system as the rest of the page.
const CHART_PALETTE = [
    '#1F6D4C', '#9C6B12', '#6B5B95', '#AE4331',
    '#3E7C8C', '#8A7F53', '#4F8F73', '#B98A5E',
    '#7C5C7C', '#5B7A8C', '#C2703F', '#6E7A4F'
];

function cssVar(name) {
    return getComputedStyle(document.documentElement).getPropertyValue(name).trim();
}

document.addEventListener('DOMContentLoaded', function() {
    loadDashboard();
    
    // Refresh button
    document.getElementById('refreshBtn').addEventListener('click', function() {
        loadDashboard();
    });
    document.getElementById('dashboardStatus').addEventListener('change', function() {
        loadDashboard();
    });
    document.getElementById('dashboardRegion').addEventListener('change', function() {
        document.cookie = `jmi_region=${this.value};path=/;max-age=31536000;SameSite=Lax`;
        loadDashboard();
    });
});

function dashboardApi(path) {
    const status = document.getElementById('dashboardStatus')?.value || 'all';
    const region = document.getElementById('dashboardRegion')?.value || 'pk';
    return `${path}?status=${encodeURIComponent(status)}&region=${encodeURIComponent(region)}`;
}

function loadDashboard() {
    updateTime();
    loadKPIs();
    loadTrendsChart();
    loadTopSkillsChart();
    loadGeoChart();
    loadSourcesChart();
    loadEmergingSkills();
    loadDecliningSkills();
    loadTopCompanies();
    loadLocationDiversity();
}

function updateTime() {
    const now = new Date();
    document.getElementById('updateTime').textContent = now.toLocaleString();
}

// Load KPIs
// Soft/rounded presentation for anonymous visitors. window.GW_AUTHED is set by
// _gating.html's overlay() macro (rendered in dashboard.html before this file
// loads); signed-in users always see the exact figure.
function fmtKpi(n) {
    if (window.GW_AUTHED || typeof n !== 'number') return n.toLocaleString();
    if (n >= 1000) return Math.floor(n / 1000) + 'K+';
    return n;
}

function loadKPIs() {
    fetch(dashboardApi('/api/dashboard/kpis'))
        .then(response => response.json())
        .then(data => {
            document.getElementById('kpiJobs').textContent = fmtKpi(data.total_jobs);
            setTrend(document.getElementById('kpiJobsTrend'), data.jobs_trend);

            document.getElementById('kpiSkills').textContent = fmtKpi(data.total_skills);
            setTrend(document.getElementById('kpiSkillsTrend'), data.skills_trend);
            
            document.getElementById('kpiSources').textContent = data.active_sources;
            document.getElementById('kpiRemote').textContent = data.remote_pct + '%';
        })
        .catch(error => {
            console.error('Error loading KPIs:', error);
        });
}

// Render a trend arrow with a semantic color class instead of bare unicode text.
function setTrend(el, arrow) {
    if (!el) return;
    el.textContent = arrow;
    el.classList.remove('trend-up', 'trend-down', 'trend-flat');
    if (arrow === '↑') el.classList.add('trend-up');
    else if (arrow === '↓') el.classList.add('trend-down');
    else el.classList.add('trend-flat');
}

// Load Job Posting Trends Chart
function loadTrendsChart() {
    fetch(dashboardApi('/api/dashboard/trends'))
        .then(response => response.json())
        .then(data => {
            const ctx = document.getElementById('trendsChart').getContext('2d');
            
            if (charts.trends) {
                charts.trends.destroy();
            }

            const accent = cssVar('--accent');
            const accentBg = cssVar('--accent-bg');
            const textSecondary = cssVar('--text-secondary');
            const gridColor = cssVar('--border-subtle');

            charts.trends = new Chart(ctx, {
                type: 'line',
                data: {
                    labels: data.labels,
                    datasets: [{
                        label: 'Jobs Posted',
                        data: data.values,
                        borderColor: accent,
                        backgroundColor: accentBg,
                        borderWidth: 2.5,
                        fill: true,
                        tension: 0.35,
                        pointRadius: 4,
                        pointHoverRadius: 6,
                        pointBackgroundColor: accent,
                        pointBorderColor: cssVar('--bg-surface'),
                        pointBorderWidth: 2
                    }]
                },
                options: {
                    responsive: true,
                    maintainAspectRatio: false,
                    plugins: {
                        legend: {
                            display: false
                        },
                        tooltip: {
                            backgroundColor: 'rgba(27, 25, 20, 0.92)',
                            padding: 12,
                            titleFont: { size: 13, weight: 'bold' },
                            bodyFont: { size: 12.5 }
                        }
                    },
                    scales: {
                        y: {
                            beginAtZero: true,
                            ticks: {
                                font: { size: 11 },
                                color: textSecondary
                            },
                            grid: {
                                color: gridColor
                            }
                        },
                        x: {
                            ticks: {
                                font: { size: 11 },
                                color: textSecondary
                            },
                            grid: {
                                display: false
                            }
                        }
                    }
                }
            });
        })
        .catch(error => {
            console.error('Error loading trends chart:', error);
        });
}

// Load Top Skills Chart
function loadTopSkillsChart() {
    fetch(dashboardApi('/api/dashboard/top-skills'))
        .then(response => response.json())
        .then(data => {
            const ctx = document.getElementById('skillsChart').getContext('2d');
            
            if (charts.skills) {
                charts.skills.destroy();
            }
            
            charts.skills = new Chart(ctx, {
                type: 'bar',
                data: {
                    labels: data.map(s => s.skill),
                    datasets: [{
                        label: 'Job Mentions',
                        data: data.map(s => s.count),
                        backgroundColor: CHART_PALETTE,
                        borderWidth: 0,
                        borderRadius: 4
                    }]
                },
                options: {
                    indexAxis: 'y',
                    responsive: true,
                    maintainAspectRatio: false,
                    plugins: {
                        legend: {
                            display: false
                        },
                        tooltip: {
                            backgroundColor: 'rgba(27, 25, 20, 0.92)',
                            padding: 12,
                            titleFont: { size: 13, weight: 'bold' },
                            bodyFont: { size: 12.5 }
                        }
                    },
                    scales: {
                        x: {
                            beginAtZero: true,
                            ticks: {
                                font: { size: 11 },
                                color: cssVar('--text-secondary')
                            },
                            grid: {
                                color: cssVar('--border-subtle')
                            }
                        },
                        y: {
                            ticks: {
                                font: { size: 11 },
                                color: cssVar('--text-secondary')
                            },
                            grid: {
                                display: false
                            }
                        }
                    }
                }
            });
        })
        .catch(error => {
            console.error('Error loading skills chart:', error);
        });
}

// Load Geographic Distribution Chart
function loadGeoChart() {
    fetch(dashboardApi('/api/dashboard/geo'))
        .then(response => response.json())
        .then(data => {
            const ctx = document.getElementById('geoChart').getContext('2d');
            
            if (charts.geo) {
                charts.geo.destroy();
            }
            
            // Take top 10
            const top10 = data.slice(0, 10);
            
            charts.geo = new Chart(ctx, {
                type: 'doughnut',
                data: {
                    labels: top10.map(g => g.country),
                    datasets: [{
                        data: top10.map(g => g.count),
                        backgroundColor: CHART_PALETTE,
                        borderWidth: 2,
                        borderColor: cssVar('--bg-surface')
                    }]
                },
                options: {
                    responsive: true,
                    maintainAspectRatio: false,
                    plugins: {
                        legend: {
                            position: 'bottom',
                            labels: {
                                font: { size: 10 },
                                padding: 10,
                                boxWidth: 12,
                                color: cssVar('--text-secondary')
                            }
                        },
                        tooltip: {
                            backgroundColor: 'rgba(27, 25, 20, 0.92)',
                            padding: 12,
                            titleFont: { size: 13, weight: 'bold' },
                            bodyFont: { size: 12.5 }
                        }
                    }
                }
            });
        })
        .catch(error => {
            console.error('Error loading geo chart:', error);
        });
}

// Load Sources Chart
function loadSourcesChart() {
    fetch(dashboardApi('/api/dashboard/sources'))
        .then(response => response.json())
        .then(data => {
            const ctx = document.getElementById('sourcesChart').getContext('2d');
            
            if (charts.sources) {
                charts.sources.destroy();
            }
            
            charts.sources = new Chart(ctx, {
                type: 'pie',
                data: {
                    labels: data.map(s => s.source),
                    datasets: [{
                        data: data.map(s => s.count),
                        backgroundColor: CHART_PALETTE,
                        borderWidth: 2,
                        borderColor: cssVar('--bg-surface')
                    }]
                },
                options: {
                    responsive: true,
                    maintainAspectRatio: false,
                    plugins: {
                        legend: {
                            position: 'bottom',
                            labels: {
                                font: { size: 10 },
                                padding: 10,
                                boxWidth: 12,
                                color: cssVar('--text-secondary')
                            }
                        },
                        tooltip: {
                            backgroundColor: 'rgba(27, 25, 20, 0.92)',
                            padding: 12,
                            titleFont: { size: 13, weight: 'bold' },
                            bodyFont: { size: 12.5 }
                        }
                    }
                }
            });
        })
        .catch(error => {
            console.error('Error loading sources chart:', error);
        });
}

// Load Emerging Skills
function loadEmergingSkills() {
    fetch(dashboardApi('/api/dashboard/emerging'))
        .then(response => response.json())
        .then(data => {
            const container = document.getElementById('emergingList');

            if (!Array.isArray(data) || data.length === 0) {
                container.innerHTML = '<p style="color: var(--text-secondary); text-align: center; padding: 2rem;">Nothing trending up right now — check back soon.</p>';
                return;
            }
            
            const html = `
                <table class="data-table">
                    <thead>
                        <tr>
                            <th>Skill</th>
                            <th>Category</th>
                            <th>Mentions</th>
                            <th>Growth</th>
                        </tr>
                    </thead>
                    <tbody>
                        ${data.map(skill => `
                            <tr>
                                <td><strong>${skill.skill}</strong></td>
                                <td><span style="font-size: 0.75rem; color: var(--text-secondary);">${skill.category || 'N/A'}</span></td>
                                <td>${skill.frequency}</td>
                                <td><span class="badge-emerging">+${skill.growth.toFixed(1)}%</span></td>
                            </tr>
                        `).join('')}
                    </tbody>
                </table>
            `;
            
            container.innerHTML = html;
        })
        .catch(error => {
            console.error('Error loading emerging skills:', error);
            document.getElementById('emergingList').innerHTML = '<p style="color: var(--danger); text-align: center; padding: 2rem;">Something went wrong loading this — try refreshing.</p>';
        });
}

// Load Declining Skills
function loadDecliningSkills() {
    fetch(dashboardApi('/api/dashboard/declining'))
        .then(response => response.json())
        .then(data => {
            const container = document.getElementById('decliningList');

            if (!Array.isArray(data) || data.length === 0) {
                container.innerHTML = '<p style="color: var(--text-secondary); text-align: center; padding: 2rem;">Nothing trending down right now — check back soon.</p>';
                return;
            }
            
            const html = `
                <table class="data-table">
                    <thead>
                        <tr>
                            <th>Skill</th>
                            <th>Category</th>
                            <th>Mentions</th>
                            <th>Decline</th>
                        </tr>
                    </thead>
                    <tbody>
                        ${data.map(skill => `
                            <tr>
                                <td><strong>${skill.skill}</strong></td>
                                <td><span style="font-size: 0.75rem; color: var(--text-secondary);">${skill.category || 'N/A'}</span></td>
                                <td>${skill.frequency}</td>
                                <td><span class="badge-declining">${skill.growth.toFixed(1)}%</span></td>
                            </tr>
                        `).join('')}
                    </tbody>
                </table>
            `;
            
            container.innerHTML = html;
        })
        .catch(error => {
            console.error('Error loading declining skills:', error);
            document.getElementById('decliningList').innerHTML = '<p style="color: var(--danger); text-align: center; padding: 2rem;">Something went wrong loading this — try refreshing.</p>';
        });
}

// Load Top Companies
function loadTopCompanies() {
    fetch(dashboardApi('/api/dashboard/companies'))
        .then(response => response.json())
        .then(data => {
            const tbody = document.querySelector('#companiesTable tbody');
            
            if (data.length === 0) {
                tbody.innerHTML = '<tr><td colspan="4" style="text-align: center; color: var(--text-secondary); padding: 2rem;">Nothing here yet — check back soon.</td></tr>';
                return;
            }
            
            const authed = window.GW_AUTHED;
            const html = data.map((company, index) => `
                <tr${authed ? '' : ' class="gw-row-gate" onclick="gwShowGate()"'}>
                    <td><strong>#${index + 1}</strong></td>
                    <td>${company.company}</td>
                    <td><strong>${company.count}</strong> jobs</td>
                    <td>
                        ${authed ? `
                        <a href="/jobs?company=${encodeURIComponent(company.company)}" 
                           class="btn" 
                           style="padding: 0.25rem 0.75rem; font-size: 0.75rem; background: var(--accent); color: white; text-decoration: none; border-radius: 6px;">
                            View Jobs
                        </a>` : `
                        <button type="button" onclick="event.stopPropagation();gwShowGate()"
                           class="btn"
                           style="padding: 0.25rem 0.75rem; font-size: 0.75rem; background: var(--accent); color: white; border: none; border-radius: 6px; cursor: pointer; font-family: inherit;">
                            View Jobs
                        </button>`}
                    </td>
                </tr>
            `).join('');
            
            tbody.innerHTML = html;
        })
        .catch(error => {
            console.error('Error loading companies:', error);
            document.querySelector('#companiesTable tbody').innerHTML = '<tr><td colspan="4" style="text-align: center; color: var(--danger); padding: 2rem;">Something went wrong loading this — try refreshing.</td></tr>';
        });
}

function loadLocationDiversity() {
    fetch(dashboardApi('/api/dashboard/location-diversity'))
        .then(response => response.json())
        .then(data => {
            const tbody = document.querySelector('#locationDiversityTable tbody');
            
            if (data.length === 0) {
                tbody.innerHTML = '<tr><td colspan="4" style="text-align: center; color: var(--text-secondary); padding: 2rem;">No multi-location postings yet.</td></tr>';
                return;
            }
            
            const pinSvg = '<svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" style="vertical-align:-2px;"><path d="M12 22s7-6.2 7-12A7 7 0 0 0 5 10c0 5.8 7 12 7 12z"></path><circle cx="12" cy="10" r="2.5"></circle></svg>';

            const authed = window.GW_AUTHED;
            const html = data.map((item, index) => `
                <tr${authed ? '' : ' class="gw-row-gate" onclick="gwShowGate()"'}>
                    <td><strong>#${index + 1}</strong></td>
                    <td>${item.company}</td>
                    <td><span style="color: var(--accent); font-weight: 600;">${pinSvg} ${item.max_locations} locations</span></td>
                    <td>${item.job_count} posting${item.job_count > 1 ? 's' : ''}</td>
                </tr>
            `).join('');
            
            tbody.innerHTML = html;
        })
        .catch(error => {
            console.error('Error loading location diversity:', error);
            document.querySelector('#locationDiversityTable tbody').innerHTML = '<tr><td colspan="4" style="text-align: center; color: var(--danger); padding: 2rem;">Something went wrong loading this — try refreshing.</td></tr>';
        });
}
