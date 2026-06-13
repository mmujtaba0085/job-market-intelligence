// Dashboard Data Loading and Chart Rendering
let charts = {};

document.addEventListener('DOMContentLoaded', function() {
    loadDashboard();
    
    // Refresh button
    document.getElementById('refreshBtn').addEventListener('click', function() {
        loadDashboard();
    });
    document.getElementById('dashboardStatus').addEventListener('change', function() {
        loadDashboard();
    });
});

function dashboardApi(path) {
    const status = document.getElementById('dashboardStatus')?.value || 'active';
    return `${path}?status=${encodeURIComponent(status)}`;
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
function loadKPIs() {
    fetch(dashboardApi('/api/dashboard/kpis'))
        .then(response => response.json())
        .then(data => {
            document.getElementById('kpiJobs').textContent = data.total_jobs.toLocaleString();
            document.getElementById('kpiJobsTrend').textContent = data.jobs_trend;
            
            document.getElementById('kpiSkills').textContent = data.total_skills.toLocaleString();
            document.getElementById('kpiSkillsTrend').textContent = data.skills_trend;
            
            document.getElementById('kpiSources').textContent = data.active_sources;
            document.getElementById('kpiRemote').textContent = data.remote_pct + '%';
        })
        .catch(error => {
            console.error('Error loading KPIs:', error);
        });
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
            
            charts.trends = new Chart(ctx, {
                type: 'line',
                data: {
                    labels: data.labels,
                    datasets: [{
                        label: 'Jobs Posted',
                        data: data.values,
                        borderColor: '#667eea',
                        backgroundColor: 'rgba(102, 126, 234, 0.1)',
                        borderWidth: 3,
                        fill: true,
                        tension: 0.4,
                        pointRadius: 5,
                        pointHoverRadius: 7,
                        pointBackgroundColor: '#667eea',
                        pointBorderColor: '#fff',
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
                            backgroundColor: 'rgba(0, 0, 0, 0.8)',
                            padding: 12,
                            titleFont: { size: 14, weight: 'bold' },
                            bodyFont: { size: 13 }
                        }
                    },
                    scales: {
                        y: {
                            beginAtZero: true,
                            ticks: {
                                font: { size: 11 }
                            },
                            grid: {
                                color: 'rgba(0, 0, 0, 0.05)'
                            }
                        },
                        x: {
                            ticks: {
                                font: { size: 11 }
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
                        backgroundColor: [
                            '#667eea', '#764ba2', '#f093fb', '#4facfe',
                            '#43e97b', '#fa709a', '#fee140', '#30cfd0',
                            '#a8edea', '#ff6b6b'
                        ],
                        borderWidth: 0
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
                            backgroundColor: 'rgba(0, 0, 0, 0.8)',
                            padding: 12,
                            titleFont: { size: 14, weight: 'bold' },
                            bodyFont: { size: 13 }
                        }
                    },
                    scales: {
                        x: {
                            beginAtZero: true,
                            ticks: {
                                font: { size: 11 }
                            },
                            grid: {
                                color: 'rgba(0, 0, 0, 0.05)'
                            }
                        },
                        y: {
                            ticks: {
                                font: { size: 11 }
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
                        backgroundColor: [
                            '#667eea', '#764ba2', '#f093fb', '#4facfe',
                            '#43e97b', '#fa709a', '#fee140', '#30cfd0',
                            '#a8edea', '#ff6b6b'
                        ],
                        borderWidth: 2,
                        borderColor: '#fff'
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
                                boxWidth: 12
                            }
                        },
                        tooltip: {
                            backgroundColor: 'rgba(0, 0, 0, 0.8)',
                            padding: 12,
                            titleFont: { size: 14, weight: 'bold' },
                            bodyFont: { size: 13 }
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
                        backgroundColor: [
                            '#667eea', '#764ba2', '#f093fb', '#4facfe',
                            '#43e97b', '#fa709a', '#fee140', '#30cfd0',
                            '#a8edea', '#ff6b6b', '#ffa07a', '#20b2aa'
                        ],
                        borderWidth: 2,
                        borderColor: '#fff'
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
                                boxWidth: 12
                            }
                        },
                        tooltip: {
                            backgroundColor: 'rgba(0, 0, 0, 0.8)',
                            padding: 12,
                            titleFont: { size: 14, weight: 'bold' },
                            bodyFont: { size: 13 }
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
                container.innerHTML = '<p style="color: #6b7280; text-align: center; padding: 2rem;">No emerging skills detected</p>';
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
                                <td><span style="font-size: 0.75rem; color: #6b7280;">${skill.category || 'N/A'}</span></td>
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
            document.getElementById('emergingList').innerHTML = '<p style="color: #ef4444; text-align: center; padding: 2rem;">Error loading data</p>';
        });
}

// Load Declining Skills
function loadDecliningSkills() {
    fetch(dashboardApi('/api/dashboard/declining'))
        .then(response => response.json())
        .then(data => {
            const container = document.getElementById('decliningList');

            if (!Array.isArray(data) || data.length === 0) {
                container.innerHTML = '<p style="color: #6b7280; text-align: center; padding: 2rem;">No declining skills detected</p>';
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
                                <td><span style="font-size: 0.75rem; color: #6b7280;">${skill.category || 'N/A'}</span></td>
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
            document.getElementById('decliningList').innerHTML = '<p style="color: #ef4444; text-align: center; padding: 2rem;">Error loading data</p>';
        });
}

// Load Top Companies
function loadTopCompanies() {
    fetch(dashboardApi('/api/dashboard/companies'))
        .then(response => response.json())
        .then(data => {
            const tbody = document.querySelector('#companiesTable tbody');
            
            if (data.length === 0) {
                tbody.innerHTML = '<tr><td colspan="4" style="text-align: center; color: #6b7280; padding: 2rem;">No data available</td></tr>';
                return;
            }
            
            const html = data.map((company, index) => `
                <tr>
                    <td><strong>#${index + 1}</strong></td>
                    <td>${company.company}</td>
                    <td><strong>${company.count}</strong> jobs</td>
                    <td>
                        <a href="/jobs?company=${encodeURIComponent(company.company)}" 
                           class="btn" 
                           style="padding: 0.25rem 0.75rem; font-size: 0.75rem; background: #667eea; color: white; text-decoration: none; border-radius: 4px;">
                            View Jobs
                        </a>
                    </td>
                </tr>
            `).join('');
            
            tbody.innerHTML = html;
        })
        .catch(error => {
            console.error('Error loading companies:', error);
            document.querySelector('#companiesTable tbody').innerHTML = '<tr><td colspan="4" style="text-align: center; color: #ef4444; padding: 2rem;">Error loading data</td></tr>';
        });
}

function loadLocationDiversity() {
    fetch(dashboardApi('/api/dashboard/location-diversity'))
        .then(response => response.json())
        .then(data => {
            const tbody = document.querySelector('#locationDiversityTable tbody');
            
            if (data.length === 0) {
                tbody.innerHTML = '<tr><td colspan="4" style="text-align: center; color: #6b7280; padding: 2rem;">No multi-location jobs found</td></tr>';
                return;
            }
            
            const html = data.map((item, index) => `
                <tr>
                    <td><strong>#${index + 1}</strong></td>
                    <td>${item.company}</td>
                    <td><span style="color: #667eea; font-weight: 600;">📍 ${item.max_locations} locations</span></td>
                    <td>${item.job_count} posting${item.job_count > 1 ? 's' : ''}</td>
                </tr>
            `).join('');
            
            tbody.innerHTML = html;
        })
        .catch(error => {
            console.error('Error loading location diversity:', error);
            document.querySelector('#locationDiversityTable tbody').innerHTML = '<tr><td colspan="4" style="text-align: center; color: #ef4444; padding: 2rem;">Error loading data</td></tr>';
        });
}
