// Filter Sidebar Interaction Logic
document.addEventListener('DOMContentLoaded', function() {
    const filterToggle = document.getElementById('filterToggle');
    const filterSidebar = document.getElementById('filterSidebar');
    const filterOverlay = document.getElementById('filterOverlay');
    const filterClose = document.getElementById('filterClose');
    const applyFiltersBtn = document.getElementById('applyFilters');
    const clearFiltersBtn = document.getElementById('clearFilters');
    const skillSearch = document.getElementById('skillSearch');
    
    // Toggle filter sidebar
    if (filterToggle) {
        filterToggle.addEventListener('click', function() {
            filterSidebar.classList.add('active');
            filterOverlay.classList.add('active');
        });
    }
    
    // Close filter sidebar
    function closeFilters() {
        filterSidebar.classList.remove('active');
        filterOverlay.classList.remove('active');
    }
    
    if (filterClose) {
        filterClose.addEventListener('click', closeFilters);
    }
    
    if (filterOverlay) {
        filterOverlay.addEventListener('click', closeFilters);
    }
    
    // Apply filters
    if (applyFiltersBtn) {
        applyFiltersBtn.addEventListener('click', function() {
            document.getElementById('filterForm').submit();
        });
    }
    
    // Clear filters
    if (clearFiltersBtn) {
        clearFiltersBtn.addEventListener('click', function() {
            // Clear all form inputs
            document.querySelectorAll('#filterForm input[type="text"]').forEach(input => input.value = '');
            document.querySelectorAll('#filterForm input[type="date"]').forEach(input => input.value = '');
            document.querySelectorAll('#filterForm select').forEach(select => select.selectedIndex = 0);
            document.querySelectorAll('#filterForm input[type="checkbox"]').forEach(checkbox => checkbox.checked = false);
            
            // Submit form to reload without filters
            document.getElementById('filterForm').submit();
        });
    }
    
    // Skill search functionality
    if (skillSearch) {
        skillSearch.addEventListener('input', function(e) {
            const searchTerm = e.target.value.toLowerCase();
            const skillItems = document.querySelectorAll('.skill-item');
            
            skillItems.forEach(item => {
                const label = item.querySelector('label').textContent.toLowerCase();
                if (label.includes(searchTerm)) {
                    item.style.display = 'flex';
                } else {
                    item.style.display = 'none';
                }
            });
        });
    }
    
    // Load skills dynamically via API
    loadSkillsFilter();
    
    function loadSkillsFilter() {
        const skillsList = document.getElementById('skillsList');
        if (!skillsList) return;
        
        fetch('/api/filters/skills')
            .then(response => {
                if (response.status === 401) {
                    skillsList.innerHTML = window.gwShowGate
                        ? '<p style="font-size:13px;color:var(--text-secondary,#666);">' +
                          '<a href="#" onclick="event.preventDefault();gwShowGate();" style="color:inherit;text-decoration:underline;">Sign in</a> to filter by skills.</p>'
                        : '<p style="font-size:13px;color:#999;">Sign in to filter by skills.</p>';
                    return null;
                }
                return response.json();
            })
            .then(skills => {
                if (skills === null) return;  // handled above (locked)
                if (skills.length === 0) {
                    skillsList.innerHTML = '<p style="color: #999; font-size: 13px;">No skills found</p>';
                    return;
                }
                
                // Group skills by category
                const grouped = {};
                skills.forEach(skill => {
                    const category = skill.category || 'other';
                    if (!grouped[category]) {
                        grouped[category] = [];
                    }
                    grouped[category].push(skill);
                });
                
                // Render skills (top 50 most common)
                const topSkills = skills.slice(0, 50);
                skillsList.innerHTML = topSkills.map(skill => `
                    <div class="skill-item">
                        <input type="checkbox" id="skill_${skill.skill}" name="skills" value="${skill.skill}">
                        <label for="skill_${skill.skill}">${skill.skill}</label>
                        <span class="skill-count">(${skill.count})</span>
                    </div>
                `).join('');
                
                // Re-check previously selected skills
                const urlParams = new URLSearchParams(window.location.search);
                const selectedSkills = urlParams.getAll('skills');
                selectedSkills.forEach(skill => {
                    const checkbox = document.getElementById(`skill_${skill}`);
                    if (checkbox) checkbox.checked = true;
                });
            })
            .catch(error => {
                console.error('Error loading skills:', error);
                skillsList.innerHTML = '<p style="color: #f00; font-size: 13px;">Error loading skills</p>';
            });
    }
});
