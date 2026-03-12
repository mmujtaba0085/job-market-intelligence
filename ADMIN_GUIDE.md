# Admin Panel - Complete Feature Guide

## 🎯 Overview
The Admin Panel provides comprehensive data management tools for normalizing and cleaning job market data.

**Access**: http://localhost:5000/admin

---

## 📋 Available Admin Panels

### 1. 🌍 Country Normalization (`/admin/normalize`)

**Purpose**: Fix "Unknown" country assignments and normalize location data

**Features**:
- **Countries Tab**:
  - View all "Unknown" country mappings
  - Preview changes before applying
  - Manual country override input
  - Batch apply country normalizations
  - Shows confidence scores for country detection
  
- **Locations Tab**:
  - Review all location-to-country mappings
  - Edit mappings for specific locations
  - Bulk location normalization
  
- **Auto-Suggest**:
  - Uses geopy to automatically suggest countries from location strings
  - Shows confidence scores
  - One-click accept/reject suggestions
  
- **Auto-Fix All**:
  - Automatically fix all "Unknown" countries using weighted voting
  - Country detector with confidence scoring
  - Batch processing with rollback support
  
**Use Cases**:
- Clean up imported job data with missing countries
- Override incorrect country detections
- Standardize country names across data sources

---

### 2. 📋 Title Normalization (`/admin/normalize-titles`)

**Purpose**: Manage job title normalizations and consolidations

**Features**:
- **KPI Dashboard**:
  - Total jobs count
  - High confidence normalizations (≥90%)
  - Medium confidence normalizations (60-89%)
  - Low confidence normalizations (<60%)
  - No normalization count
  
- **Top Consolidations Table**:
  - Shows which titles were merged (e.g., "Software Engineering Intern" → "Software Engineer Intern")
  - Displays job count, variant count, and average confidence
  - "View Variants" button to see all variants
  - Color-coded confidence badges
  
- **Low-Confidence Review** (if any exist):
  - Lists normalizations below 60% confidence
  - Accept/Reject buttons for each
  - Shows original title, normalized form, confidence %, and job count
  
- **Manual Mapping Form**:
  - Create custom title normalization rules
  - Preview affected jobs before applying
  - Shows sample jobs that will be updated
  - One-click apply with 100% confidence
  
- **Variants Modal**:
  - Click "View Variants" on any consolidated title
  - See all original variants and their job counts
  - View confidence scores for each variant
  
- **Export Mappings**:
  - Download all title mappings as CSV
  - Includes: raw_title, normalized_title, confidence
  - Use for backup, analysis, or sharing

**Use Cases**:
- Consolidate fragmented job titles (e.g., "SWE Intern", "Software Engineering Intern" → "Software Engineer Intern")
- Review and approve low-confidence normalizations
- Create custom title mapping rules
- Export title taxonomy for reporting

---

## 📊 System Statistics (Main Dashboard)

The main admin dashboard (`/admin`) shows at-a-glance metrics:

- **Total Jobs**: Overall job count in database
- **Unknown Countries**: Jobs needing country assignment
- **Normalized Titles**: Jobs with normalized title mappings
- **Low-Confidence Titles**: Normalizations needing review

---

## ⚡ Quick Actions

From the main admin dashboard, you can quickly access:

- **📈 View Dashboard**: BI dashboard with analytics
- **📋 Title Analytics**: Detailed title breakdown and trends
- **🎯 Skills Intelligence**: Skills co-occurrence and trends
- **💾 Export Title Mappings**: Download CSV of all title mappings

---

## 🔧 Technical Details

### Country Normalization Workflow
1. Jobs ingested with location data
2. Country detector uses weighted voting (5 methods)
3. Low-confidence results marked for review
4. Admin can override or auto-fix unknowns
5. Changes applied to database immediately

### Title Normalization Workflow
1. Jobs ingested with original title
2. Title normalizer uses weighted voting (4 methods)
3. Confidence score calculated (0.0-1.0)
4. High-confidence (≥60%) auto-applied
5. Low-confidence flagged for admin review
6. Admin can accept, reject, or create manual mappings

### Confidence Scoring

**Country Detection**:
- 1.0 (100%): Exact match in country mappings
- 0.8-0.9 (80-90%): Pattern match or keyword detection
- 0.6-0.8 (60-80%): Geolocation or similarity match
- <0.6 (<60%): Low confidence, needs review

**Title Normalization**:
- 1.0 (100%): Exact mapping from curated rules
- 0.8-0.9 (80-90%): Abbreviation expansion or pattern match
- 0.6-0.8 (60-80%): Similarity matching
- 0.0 (0%): No normalization (kept as original)

---

## 📝 Best Practices

### Country Normalization
1. **Review Unknown Countries First**: Start with `/admin/normalize` Countries tab
2. **Use Auto-Suggest**: Click "Suggest Country" for geopy-powered suggestions
3. **Preview Before Apply**: Always preview changes to verify correctness
4. **Batch Operations Last**: Use "Auto-Fix All" only after reviewing samples

### Title Normalization
1. **Check Top Consolidations**: Verify common normalizations make sense
2. **Review Low-Confidence**: If any exist, review and accept/reject individually
3. **Create Manual Mappings**: Add custom mappings for domain-specific titles
4. **Export Regularly**: Download CSV backups of your mappings
5. **Monitor Confidence**: Aim for 90%+ confidence on critical title normalizations

---

## 🚨 Safety Features

### Rollback Protection
- All changes are previewed before applying
- Manual mappings can be overridden by re-applying with different values
- Export/import allows backup and restore of mappings

### Confidence Thresholds
- Auto-apply threshold: 60% (configurable in code)
- Low-confidence normalizations are flagged, not auto-applied
- Manual mappings always get 100% confidence

### Data Integrity
- Original values always preserved (title, location fields)
- Normalized values stored separately (normalized_title, country)
- Confidence scores tracked for audit trail

---

## 🎓 Example Workflows

### Workflow 1: Clean Up Unknown Countries
1. Go to `/admin`
2. Click "Country Normalization" card
3. Click "Auto-Fix All Unknown" button
4. Review summary (updated count, confidence breakdown)
5. Verify changes in Countries tab
6. Manually fix any remaining unknowns if needed

### Workflow 2: Consolidate Job Titles
1. Go to `/admin`
2. Click "Title Normalization" card
3. Review "Top Consolidations" table
4. Click "View Variants" on any title to see what was merged
5. If satisfied, no action needed (already applied)
6. If not satisfied, create manual mapping to override

### Workflow 3: Add Custom Title Mapping
1. Go to `/admin/normalize-titles`
2. Scroll to "Add Manual Mapping" section
3. Enter original title (e.g., "SWE Intern")
4. Enter normalized title (e.g., "Software Engineer Intern")
5. Click "Preview Changes"
6. Review affected jobs count and samples
7. Click "Apply Mapping"
8. Refresh page to see updated consolidations

---

## 🔗 Navigation

- **Main Admin**: http://localhost:5000/admin
- **Country Admin**: http://localhost:5000/admin/normalize
- **Title Admin**: http://localhost:5000/admin/normalize-titles

All admin panels accessible via "⚙️ Admin" link in top navigation.

---

## Status: ✅ Fully Operational

Both admin panels are fully functional and integrated into the main dashboard.
