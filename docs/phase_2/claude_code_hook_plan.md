# Claude Code API Hook Implementation Plan

## Overview
Modify Claude Code to log raw API requests for research and analysis purposes. This involves de-minifying the current version, adding logging hooks, and creating a drop-in replacement.

## Prerequisites
- Claude Code already installed globally via npm
- Node.js and npm development environment
- Text editor with JavaScript support
- Basic knowledge of JavaScript and npm package structure

## Phase 1: Extract and Analyze Current Claude Code

### Step 1.1: Locate Claude Code Installation
```bash
# Find global npm installation path
npm list -g claude-code

# Navigate to the package directory
cd $(npm root -g)/claude-code

# OR find the actual executable
which claude-code
# Then navigate to its parent directory
```

### Step 1.2: Identify Main Entry Point
```bash
# Check package.json for main entry point
cat package.json | grep -E '"main"|"bin"'

# Common locations for the minified code:
# - cli.js
# - cli.mjs  
# - dist/cli.js
# - lib/cli.js

# List all .js and .mjs files to identify the main executable
find . -name "*.js" -o -name "*.mjs" | head -20
```

### Step 1.3: Create Working Directory
```bash
# Create project directory
mkdir ~/claude-code-hook-project
cd ~/claude-code-hook-project

# Copy the Claude Code package for modification
cp -r $(npm root -g)/claude-code ./original-claude-code

# Create workspace for modified version
mkdir modified-claude-code
cp -r original-claude-code/* modified-claude-code/
```

## Phase 2: De-minification Process

### Step 2.1: Identify Minified Files
```bash
cd modified-claude-code

# Find the main minified file (usually largest .js/.mjs file)
ls -la *.js *.mjs 2>/dev/null | sort -k5 -nr

# Check file characteristics
file cli.js  # or cli.mjs or whatever the main file is
wc -l cli.js  # Line count (minified files typically have very few lines)
```

### Step 2.2: Basic De-minification
Use one of these approaches:

**Option A: Online Tools (Quick)**
- Copy minified code to https://beautifier.io/
- Paste result back to file
- This provides basic formatting but no variable renaming

**Option B: CLI Tools (Better)**
```bash
# Install js-beautify
npm install -g js-beautify

# De-minify the main file
js-beautify cli.js -r -s 2 -j -B -n

# OR if it's an ES module (.mjs)
js-beautify cli.mjs -r -s 2 -j -B -n
```

**Option C: Advanced De-minification (Best)**
```bash
# Install webcrack for better de-minification
npm install -g @j4k0xb/webcrack

# Attempt advanced de-minification
webcrack cli.js --output ./decompiled/

# This may extract modules and rename variables
```

### Step 2.3: Module Extraction (If Single Large File)
If the de-minified code is still one huge file:

```bash
# Create modules directory
mkdir src

# Look for major functional boundaries in the code:
# - Look for large object literals that might be separate modules
# - Search for patterns like: var moduleX = function() { ... }
# - Find class definitions or function clusters

# Manually extract major sections into separate files:
# - api-client.js (for API calls)
# - cli-handler.js (for command line interface)
# - auth.js (for authentication)
# - tools.js (for tool definitions)
```

## Phase 3: Locate API Call Points

### Step 3.1: Search for API Endpoints
```bash
# Search for Claude API endpoints
grep -r "api\.anthropic\.com" .
grep -r "claude" . | grep -i "api\|endpoint\|url"

# Search for common HTTP client patterns
grep -r -E "(fetch|axios|request|http)" . | grep -v node_modules

# Search for authentication patterns
grep -r -E "(bearer|token|auth)" . | grep -v node_modules

# Look for streaming patterns (Claude API uses streaming)
grep -r -E "(stream|chunk|sse|event-source)" . | grep -v node_modules
```

### Step 3.2: Identify Request Construction
Look for patterns like:
```javascript
// Common patterns to search for:
grep -r -E "(method.*POST|Content-Type.*json)" .
grep -r -E "(messages|model|stream)" .
grep -r -E "(temperature|max_tokens)" .
```

### Step 3.3: Find Exact Hook Points
Once you identify the API call location, look for:
- Where the request body is constructed
- Where headers are set (especially Authorization)
- The actual network call (fetch, axios.post, etc.)

## Phase 4: Add Logging Hook

### Step 4.1: Create Logging Function
Add this function near the top of the main file:

```javascript
// Add this logging function
function logClaudeRequest(requestData) {
    const fs = require('fs');
    const path = require('path');
    
    // Create logs directory if it doesn't exist
    const logsDir = path.join(process.cwd(), 'claude-requests');
    if (!fs.existsSync(logsDir)) {
        fs.mkdirSync(logsDir, { recursive: true });
    }
    
    // Create filename with timestamp
    const timestamp = Date.now();
    const filename = path.join(logsDir, `request-${timestamp}.json`);
    
    // Log the request data
    fs.writeFileSync(filename, JSON.stringify(requestData, null, 2));
    console.log(`[DEBUG] Logged request to ${filename}`);
}
```

### Step 4.2: Insert Hook at API Call Point
Find the location where the API request is made and add the logging call:

```javascript
// Example of what to look for and how to modify:
// BEFORE:
const response = await fetch(apiUrl, {
    method: 'POST',
    headers: headers,
    body: JSON.stringify(requestBody)
});

// AFTER:
// Add logging hook here
logClaudeRequest({
    url: apiUrl,
    method: 'POST', 
    headers: headers,
    body: requestBody,
    timestamp: new Date().toISOString()
});

const response = await fetch(apiUrl, {
    method: 'POST',
    headers: headers, 
    body: JSON.stringify(requestBody)
});
```

## Phase 5: Testing and Deployment

### Step 5.1: Test Modified Version
```bash
# Test that the modified version still works
cd modified-claude-code
node cli.js --version  # or whatever the main command is

# Try a simple command to verify functionality
node cli.js --help

# Test actual Claude Code functionality (if safe)
node cli.js "Hello, can you help me test this?"
```

### Step 5.2: Check Logging Output
```bash
# Verify logs are being created
ls -la claude-requests/
cat claude-requests/request-*.json
```

### Step 5.3: Create Drop-in Replacement
```bash
# Option A: Create symlink (easiest)
# First backup original
sudo mv $(which claude-code) $(which claude-code).backup

# Create symlink to modified version
sudo ln -s ~/claude-code-hook-project/modified-claude-code/cli.js /usr/local/bin/claude-code

# OR Option B: Replace npm package (more complex)
cd ~/claude-code-hook-project/modified-claude-code
npm pack  # Creates a .tgz file
sudo npm install -g ./claude-code-*.tgz
```

## Phase 6: Validation and Cleanup

### Step 6.1: Validate Drop-in Replacement
```bash
# Test that claude-code command works normally
claude-code --version
claude-code --help

# Run a simple test to ensure logging works
claude-code "What is 2+2?"

# Check that logs are being created
ls -la claude-requests/
```

### Step 6.2: Create Restoration Script
```bash
# Create restore-original.sh
cat > restore-original.sh << 'EOF'
#!/bin/bash
# Restore original Claude Code
sudo rm -f $(which claude-code)
sudo mv $(which claude-code).backup $(which claude-code)
echo "Restored original Claude Code"
EOF

chmod +x restore-original.sh
```

## Troubleshooting Tips

### Common Issues:

1. **Minified code too complex**
   - Try different de-minification tools
   - Focus on finding API calls rather than full de-minification
   - Use string search patterns to locate key functions

2. **Module imports/exports broken**
   - Check if original uses ES modules (.mjs) vs CommonJS (.js)
   - Ensure require() vs import statements are consistent
   - Verify all dependencies are still accessible

3. **API endpoint not found**
   - Search for patterns like: `/v1/`, `messages`, `completions`
   - Look in network-related files or modules
   - Check for base64 encoded endpoints

4. **Logging not working**
   - Verify file permissions for log directory
   - Check if process.cwd() is correct location
   - Add console.log statements to confirm hook is being called

### Recovery:
If anything breaks:
```bash
# Restore original Claude Code
sudo rm -f $(which claude-code)
sudo mv $(which claude-code).backup $(which claude-code)

# Or reinstall from npm
npm uninstall -g claude-code
npm install -g claude-code
```

## Expected Output

After successful implementation, you should have:
- Modified Claude Code that functions identically to original
- JSON files in `claude-requests/` directory containing:
  - Full request bodies sent to Claude API
  - Headers (including auth tokens)  
  - Timestamps for each request
  - Response metadata (optionally)

This will provide valuable insights into:
- How Claude Code constructs prompts
- What system messages and instructions it uses
- Tool calling schemas and parameters
- Authentication and rate limiting patterns

## Next Steps

Once this is working:
1. Run the universal agentic debugging task
2. Analyze the generated request logs
3. Compare with other systems' approaches
4. Extract Claude Code's prompting patterns for TPSL implementation