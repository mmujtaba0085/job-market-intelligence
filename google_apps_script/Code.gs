/**
 * ============================================================================
 * JOB CLICK TRACKER - GOOGLE APPS SCRIPT WEB APP (v2.0)
 * ============================================================================
 * 
 * SETUP INSTRUCTIONS:
 * 
 * 1. Create a Google Apps Script project bound to your Tracker spreadsheet:
 *    - Open the Tracker spreadsheet
 *    - Extensions > Apps Script
 *    - Paste this code into Code.gs
 * 
 * 2. Set up Script Properties (Project Settings > Script Properties):
 *    - Key: TRACKER_TOKEN
 *    - Value: a long random string (e.g., "a8f3d9c2e1b4f7a6...")
 *    - This same token must be used in your Python exporter
 * 
 * 3. Deploy as Web App:
 *    - Click Deploy > New deployment
 *    - Type: Web app
 *    - Description: "Job Click Tracker v2"
 *    - Execute as: Me
 *    - Who has access: Anyone
 *    - Click Deploy
 *    - Copy the Web App URL (ends with /exec)
 *    - Save this URL as TRACKER_DEPLOYMENT_BASE_URL in your Python .env
 * 
 * 4. After any code changes:
 *    - Deploy > Manage deployments
 *    - Click Edit (pencil icon)
 *    - Version: New version
 *    - Click Deploy
 * 
 * TRACKER SPREADSHEET STRUCTURE:
 * 
 * Tab "Docs":
 *   Headers: doc_key | spreadsheet_id | country_name
 *   Example: ca | 1ABC...xyz | Canada
 * 
 * Tab "Directory":
 *   Headers: doc_key | country | tab_name | link_id | title | company | location | apply_url | tracking_url | clicks
 *   This is the master directory storing ALL job data and click counts
 * 
 * Tab "Clicks":
 *   Headers: ts | doc_key | country | tab_name | link_id | apply_url | user_agent
 * 
 * Tab "CountryTotals":
 *   Headers: country | total_clicks | last_updated
 * 
 * TRACKING URL FORMAT:
 *   https://script.google.com/macros/s/<DEPLOYMENT_ID>/exec?doc=ca&id=job_12345&token=<SECRET>
 * 
 * SECURITY FEATURES:
 *   - Token validation (all requests must include valid token)
 *   - Anti-spam: same link can only be clicked once per 10 seconds
 *   - User agent tracking for analytics
 *   - Top-level redirect (breaks out of iframes to avoid "refused to connect" errors)
 * 
 * ARCHITECTURE CHANGES (v2.0):
 *   - Job lookup now uses Tracker->Directory tab (not country spreadsheets)
 *   - In-memory map for fast Directory lookups
 *   - Click counts stored ONLY in Directory tab
 *   - Country spreadsheets contain NO apply URLs or click counts (privacy/security)
 *   - Top-level redirect prevents iframe blocking issues
 * 
 * ============================================================================
 */

/**
 * Main entry point for GET requests (click tracking)
 */
function doGet(e) {
  try {
    // ========================================================================
    // 1. VALIDATE PARAMETERS
    // ========================================================================
    
    const docKey = e.parameter.doc;
    const linkId = e.parameter.id;
    const providedToken = e.parameter.token;
    const userAgent = e.parameter.ua || "";
    
    // Get secret token from Script Properties
    const scriptProps = PropertiesService.getScriptProperties();
    const validToken = scriptProps.getProperty('TRACKER_TOKEN');
    
    if (!validToken) {
      return createErrorPage('Configuration Error', 'TRACKER_TOKEN not set in Script Properties');
    }
    
    // Validate required parameters
    if (!docKey) {
      return createErrorPage('Missing Parameter', 'doc parameter is required');
    }
    
    if (!linkId) {
      return createErrorPage('Missing Parameter', 'id parameter is required');
    }
    
    // Security: Validate token
    if (providedToken !== validToken) {
      Logger.log('Unauthorized access attempt: invalid token');
      return createErrorPage('Unauthorized', 'Invalid token');
    }
    
    // ========================================================================
    // 2. LOOKUP COUNTRY NAME FROM DOCS TAB
    // ========================================================================
    
    const trackerSheet = SpreadsheetApp.getActiveSpreadsheet();
    const docsTab = trackerSheet.getSheetByName('Docs');
    
    if (!docsTab) {
      return createErrorPage('Configuration Error', 'Docs tab not found in Tracker spreadsheet');
    }
    
    // Find the doc_key in column A
    const docsData = docsTab.getDataRange().getValues();
    const docsHeaders = docsData[0].map(h => String(h).toLowerCase().trim());
    
    const docKeyColIdx = docsHeaders.indexOf('doc_key');
    const countryColIdx = docsHeaders.indexOf('country_name');
    
    if (docKeyColIdx === -1 || countryColIdx === -1) {
      return createErrorPage('Configuration Error', 'Docs tab missing required columns');
    }
    
    let countryName = null;
    
    for (let i = 1; i < docsData.length; i++) {
      if (String(docsData[i][docKeyColIdx]).trim() === docKey) {
        countryName = String(docsData[i][countryColIdx]).trim();
        break;
      }
    }
    
    if (!countryName) {
      return createErrorPage('Invalid Document', `Unknown doc_key: ${docKey}`);
    }
    
    // ========================================================================
    // 3. LOAD DIRECTORY TAB INTO MEMORY
    // ========================================================================
    
    const directoryTab = trackerSheet.getSheetByName('Directory');
    
    if (!directoryTab) {
      return createErrorPage('Configuration Error', 'Directory tab not found in Tracker spreadsheet');
    }
    
    const directoryData = directoryTab.getDataRange().getValues();
    
    if (directoryData.length < 2) {
      return createErrorPage('Configuration Error', 'Directory tab is empty');
    }
    
    // Parse headers
    const dirHeaders = directoryData[0].map(h => String(h).toLowerCase().trim());
    const dirDocKeyIdx = dirHeaders.indexOf('doc_key');
    const dirLinkIdIdx = dirHeaders.indexOf('link_id');
    const dirTabNameIdx = dirHeaders.indexOf('tab_name');
    const dirTitleIdx = dirHeaders.indexOf('title');
    const dirCompanyIdx = dirHeaders.indexOf('company');
    const dirApplyUrlIdx = dirHeaders.indexOf('apply_url');
    const dirClicksIdx = dirHeaders.indexOf('clicks');
    
    if (dirDocKeyIdx === -1 || dirLinkIdIdx === -1 || dirApplyUrlIdx === -1 || dirClicksIdx === -1) {
      return createErrorPage('Configuration Error', 'Directory tab missing required columns');
    }
    
    // Build in-memory map: {doc_key + "_" + link_id: {row, apply_url, clicks, tab_name, title, company}}
    const directoryMap = {};
    
    for (let i = 1; i < directoryData.length; i++) {
      const rowDocKey = String(directoryData[i][dirDocKeyIdx]).trim();
      const rowLinkId = String(directoryData[i][dirLinkIdIdx]).trim();
      const key = `${rowDocKey}_${rowLinkId}`;
      
      directoryMap[key] = {
        row: i + 1,  // 1-based row index
        apply_url: String(directoryData[i][dirApplyUrlIdx]).trim(),
        clicks: directoryData[i][dirClicksIdx],
        tab_name: String(directoryData[i][dirTabNameIdx]).trim(),
        title: dirTitleIdx !== -1 ? String(directoryData[i][dirTitleIdx]).trim() : '',
        company: dirCompanyIdx !== -1 ? String(directoryData[i][dirCompanyIdx]).trim() : ''
      };
    }
    
    // ========================================================================
    // 4. LOOKUP JOB IN DIRECTORY
    // ========================================================================
    
    const lookupKey = `${docKey}_${linkId}`;
    const job = directoryMap[lookupKey];
    
    if (!job) {
      Logger.log(`Job not found in Directory: ${lookupKey}`);
      return createErrorPage('Not Found', `Job listing ${linkId} not found`);
    }
    
    const applyUrl = job.apply_url;
    const tabName = job.tab_name;
    
    if (!applyUrl || !applyUrl.startsWith('http')) {
      return createErrorPage('Invalid URL', 'Job listing has invalid apply URL');
    }
    
    // ========================================================================
    // 5. ANTI-SPAM CHECK (10 second cooldown)
    // ========================================================================
    
    const cache = CacheService.getScriptCache();
    const cacheKey = `clk:${docKey}:${linkId}`;
    const cached = cache.get(cacheKey);
    
    if (cached) {
      // This link was clicked within the last 10 seconds - redirect but don't log
      Logger.log(`Anti-spam: Skipping duplicate click for ${docKey}/${linkId}`);
      return createJobLandingPage(applyUrl, job.title, job.company, job.tab_name);
    }
    
    // ========================================================================
    // 6. INCREMENT CLICKS IN DIRECTORY
    // ========================================================================
    
    const currentClicks = job.clicks;
    const newClicks = (isNaN(currentClicks) || currentClicks === '') ? 1 : Number(currentClicks) + 1;
    directoryTab.getRange(job.row, dirClicksIdx + 1).setValue(newClicks);
    
    // ========================================================================
    // 7. LOG CLICK
    // ========================================================================
    
    // Set anti-spam cache (10 seconds)
    cache.put(cacheKey, 'true', 10);
    
    // Append to Clicks tab
    const clicksTab = getOrCreateSheet(trackerSheet, 'Clicks', 
      ['ts', 'doc_key', 'country', 'tab_name', 'link_id', 'apply_url', 'user_agent']);
    
    clicksTab.appendRow([
      new Date(),
      docKey,
      countryName,
      tabName,
      linkId,
      applyUrl,
      userAgent
    ]);
    
    // ========================================================================
    // 8. UPDATE COUNTRY TOTALS
    // ========================================================================
    
    const totalsTab = getOrCreateSheet(trackerSheet, 'CountryTotals',
      ['country', 'total_clicks', 'last_updated']);
    
    const totalsData = totalsTab.getDataRange().getValues();
    let countryRowIdx = -1;
    
    // Find country row
    for (let i = 1; i < totalsData.length; i++) {
      if (String(totalsData[i][0]).trim() === countryName) {
        countryRowIdx = i + 1; // 1-based
        break;
      }
    }
    
    if (countryRowIdx === -1) {
      // Country not found, create new row
      totalsTab.appendRow([countryName, 1, new Date()]);
    } else {
      // Increment existing counter
      const currentTotal = totalsData[countryRowIdx - 1][1];
      const newTotal = (isNaN(currentTotal) || currentTotal === '') ? 1 : Number(currentTotal) + 1;
      totalsTab.getRange(countryRowIdx, 2).setValue(newTotal);
      totalsTab.getRange(countryRowIdx, 3).setValue(new Date());
    }
    
    Logger.log(`Tracked click: ${docKey}/${linkId} -> ${applyUrl} (new total: ${newClicks})`);
    
    // ========================================================================
    // 9. SHOW JOB LANDING PAGE WITH LINK TO POSTING
    // ========================================================================
    
    return createJobLandingPage(applyUrl, job.title, job.company, job.tab_name);
    
  } catch (error) {
    Logger.log(`Error in doGet: ${error}`);
    return createErrorPage('Internal Error', 'An unexpected error occurred');
  }
}

/**
 * Get or create a sheet with headers
 */
function getOrCreateSheet(spreadsheet, sheetName, headers) {
  let sheet = spreadsheet.getSheetByName(sheetName);
  
  if (!sheet) {
    sheet = spreadsheet.insertSheet(sheetName);
    if (headers && headers.length > 0) {
      sheet.getRange(1, 1, 1, headers.length).setValues([headers]);
      sheet.getRange(1, 1, 1, headers.length).setFontWeight('bold');
      sheet.setFrozenRows(1);
    }
  }
  
  return sheet;
}

/**
 * Create HTML landing page with job details and clear call-to-action
 * Shows a button to open the job posting (works around Apps Script iframe restrictions)
 */
function createJobLandingPage(url, jobTitle, company, tabName) {
  // Safely escape for HTML
  const safeUrl = String(url).replace(/"/g, '&quot;');
  const safeTitle = String(jobTitle || 'Job Opportunity').replace(/</g, '&lt;').replace(/>/g, '&gt;');
  const safeCompany = String(company || 'Company').replace(/</g, '&lt;').replace(/>/g, '&gt;');
  const safeCategory = String(tabName || '').replace(/</g, '&lt;').replace(/>/g, '&gt;');
  
  const html = `
    <!DOCTYPE html>
    <html>
      <head>
        <meta charset="UTF-8">
        <meta name="viewport" content="width=device-width, initial-scale=1.0">
        <title>${safeTitle} at ${safeCompany}</title>
        <style>
          * {
            margin: 0;
            padding: 0;
            box-sizing: border-box;
          }
          body {
            font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, "Helvetica Neue", Arial, sans-serif;
            background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
            min-height: 100vh;
            display: flex;
            align-items: center;
            justify-content: center;
            padding: 20px;
          }
          .container {
            background: white;
            padding: 3rem 2.5rem;
            border-radius: 16px;
            box-shadow: 0 20px 60px rgba(0,0,0,0.3);
            max-width: 500px;
            width: 100%;
            text-align: center;
          }
          .icon {
            font-size: 3rem;
            margin-bottom: 1rem;
          }
          h1 {
            color: #1a202c;
            font-size: 1.5rem;
            margin-bottom: 0.5rem;
            line-height: 1.3;
          }
          .company {
            color: #4a5568;
            font-size: 1.1rem;
            margin-bottom: 0.5rem;
            font-weight: 500;
          }
          .category {
            display: inline-block;
            background: #edf2f7;
            color: #4a5568;
            padding: 0.4rem 1rem;
            border-radius: 20px;
            font-size: 0.9rem;
            margin-bottom: 2rem;
          }
          .cta-button {
            display: inline-block;
            background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
            color: white;
            padding: 1rem 2.5rem;
            border-radius: 8px;
            text-decoration: none;
            font-weight: 600;
            font-size: 1.1rem;
            transition: transform 0.2s, box-shadow 0.2s;
            box-shadow: 0 4px 12px rgba(102, 126, 234, 0.4);
          }
          .cta-button:hover {
            transform: translateY(-2px);
            box-shadow: 0 6px 20px rgba(102, 126, 234, 0.6);
          }
          .cta-button:active {
            transform: translateY(0);
          }
          .note {
            margin-top: 2rem;
            color: #718096;
            font-size: 0.85rem;
            line-height: 1.6;
          }
        </style>
      </head>
      <body>
        <div class="container">
          <div class="icon">💼</div>
          <h1>${safeTitle}</h1>
          <div class="company">${safeCompany}</div>
          ${safeCategory ? '<div class="category">' + safeCategory + '</div>' : ''}
          
          <a href="${safeUrl}" class="cta-button" target="_top" rel="noopener noreferrer">
            View Job Posting →
          </a>
          
          <div class="note">
            Click the button above to open the job posting.<br>
            The link will open in a new window.
          </div>
        </div>
      </body>
    </html>
  `;
  
  return HtmlService.createHtmlOutput(html)
    .setTitle(safeTitle + ' at ' + safeCompany)
    .setXFrameOptionsMode(HtmlService.XFrameOptionsMode.ALLOWALL);
}

/**
 * Create HTML error page
 */
function createErrorPage(title, message) {
  const html = `
    <!DOCTYPE html>
    <html>
      <head>
        <style>
          body {
            font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
            display: flex;
            justify-content: center;
            align-items: center;
            height: 100vh;
            margin: 0;
            background: #f5f5f5;
          }
          .container {
            text-align: center;
            padding: 2rem;
            background: white;
            border-radius: 8px;
            box-shadow: 0 2px 8px rgba(0,0,0,0.1);
            max-width: 500px;
          }
          h1 {
            color: #d93025;
            margin: 0 0 1rem 0;
            font-size: 1.5rem;
          }
          p {
            color: #5f6368;
            line-height: 1.5;
          }
        </style>
      </head>
      <body>
        <div class="container">
          <h1>${title}</h1>
          <p>${message}</p>
        </div>
      </body>
    </html>
  `;
  
  return HtmlService.createHtmlOutput(html);
}

/**
 * Test function - run this to verify Script Properties are set
 * (Tools > Script editor > Select function > Run)
 */
function testConfiguration() {
  const scriptProps = PropertiesService.getScriptProperties();
  const token = scriptProps.getProperty('TRACKER_TOKEN');
  
  if (!token) {
    Logger.log('❌ TRACKER_TOKEN not set!');
    Logger.log('Set it in Project Settings > Script Properties');
    return;
  }
  
  Logger.log('✅ TRACKER_TOKEN is set');
  Logger.log('Token length: ' + token.length + ' characters');
  
  // Check if required tabs exist
  const ss = SpreadsheetApp.getActiveSpreadsheet();
  const requiredTabs = ['Docs', 'Directory', 'Clicks', 'CountryTotals'];
  
  requiredTabs.forEach(function(tabName) {
    const sheet = ss.getSheetByName(tabName);
    if (sheet) {
      const rowCount = sheet.getLastRow();
      Logger.log('✅ Tab "' + tabName + '" exists (' + rowCount + ' rows)');
    } else {
      if (tabName === 'Directory') {
        Logger.log('❌ Tab "' + tabName + '" missing - REQUIRED! Create it with Python exporter.');
      } else {
        Logger.log('⚠️  Tab "' + tabName + '" missing (will be auto-created on first click)');
      }
    }
  });
  
  Logger.log('Configuration check complete!');
}
