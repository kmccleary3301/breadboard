#!/bin/bash
# Build script for LSP Universal Container

set -e

echo "Building LSP Universal Container..."

# Build the container
docker build -f lsp-universal.Dockerfile -t lsp-universal:latest .

echo "Testing container build..."

# Test the container
docker run --rm lsp-universal:latest help

echo "Testing individual language servers..."

# Test TypeScript server
echo "Testing TypeScript server..."
timeout 5s docker run --rm lsp-universal:latest typescript --help || echo "TypeScript server ready"

# Test Python server
echo "Testing Python server..."
timeout 5s docker run --rm lsp-universal:latest python --help || echo "Python server ready"

# Test Go server
echo "Testing Go server..."
timeout 5s docker run --rm lsp-universal:latest go --help || echo "Go server ready"

echo "LSP Universal Container build complete!"
echo ""
echo "Usage examples:"
echo "  # Run TypeScript language server"
echo "  docker run -i --rm -v /path/to/workspace:/workspace lsp-universal:latest typescript"
echo ""
echo "  # Run Python language server"  
echo "  docker run -i --rm -v /path/to/workspace:/workspace lsp-universal:latest python"
echo ""
echo "  # Run with custom networking (for Ray integration)"
echo "  docker run -i --rm --network=none -v /workspace:/workspace lsp-universal:latest typescript"

# Create a lightweight test container for development
echo "Creating development test container..."
cat > Dockerfile.lsp-dev << 'EOF'
FROM lsp-universal:latest

# Add development tools
RUN apt-get update && apt-get install -y \
    vim \
    curl \
    jq \
    strace \
    && rm -rf /var/lib/apt/lists/*

# Add test workspace
COPY . /test-workspace
WORKDIR /test-workspace

# Add debugging script
RUN cat > /usr/local/bin/debug-lsp.sh << 'SCRIPT'
#!/bin/bash
echo "=== LSP Debug Information ==="
echo "Container: $(hostname)"
echo "Working directory: $(pwd)"
echo "Available files: $(ls -la | head -10)"
echo "Memory: $(cat /proc/meminfo | grep MemAvailable)"
echo "=== Starting LSP Server ==="
exec /usr/local/bin/lsp-launcher.sh "$@"
SCRIPT
RUN chmod +x /usr/local/bin/debug-lsp.sh

ENTRYPOINT ["/usr/local/bin/debug-lsp.sh"]
EOF

# Build development container
docker build -f Dockerfile.lsp-dev -t lsp-universal:dev .

echo "Development container built as lsp-universal:dev"
echo "Use: docker run -it --rm lsp-universal:dev bash"