# Universal LSP Server Container
# Includes multiple language servers for distributed LSP management
FROM ubuntu:22.04

# Avoid interactive prompts during package installation
ENV DEBIAN_FRONTEND=noninteractive

# Install base dependencies
RUN apt-get update && apt-get install -y \
    curl \
    wget \
    git \
    build-essential \
    pkg-config \
    python3 \
    python3-pip \
    python3-venv \
    nodejs \
    npm \
    default-jdk \
    golang-go \
    ruby \
    ruby-dev \
    dotnet6 \
    clang \
    clangd \
    cmake \
    ca-certificates \
    gnupg \
    lsb-release \
    && rm -rf /var/lib/apt/lists/*

# Install Rust
RUN curl --proto '=https' --tlsv1.2 -sSf https://sh.rustup.rs | sh -s -- -y
ENV PATH="/root/.cargo/bin:${PATH}"

# Install language servers via package managers

# TypeScript Language Server
RUN npm install -g typescript typescript-language-server @typescript-eslint/eslint-plugin @typescript-eslint/parser eslint

# Python Language Server (Pyright) + Ruff
RUN npm install -g pyright
RUN pip3 install ruff ruff-lsp mypy black isort

# Go Language Server (gopls)
RUN go install golang.org/x/tools/gopls@latest

# Rust Language Server (rust-analyzer)
RUN rustup component add rust-analyzer rustfmt clippy

# Ruby Language Server
RUN gem install ruby-lsp solargraph

# C# Language Server
RUN dotnet tool install --global csharp-ls
ENV PATH="/root/.dotnet/tools:${PATH}"

# Java Language Server (Eclipse JDT LS)
WORKDIR /opt
RUN wget -O jdtls.tar.gz "https://download.eclipse.org/jdtls/milestones/1.26.0/jdt-language-server-1.26.0-202307271613.tar.gz" \
    && mkdir -p jdtls \
    && tar -xzf jdtls.tar.gz -C jdtls \
    && rm jdtls.tar.gz
ENV JDTLS_HOME="/opt/jdtls"

# Create a startup script for JDTLS
RUN echo '#!/bin/bash\njava -Declipse.application=org.eclipse.jdt.ls.core.id1 -Dosgi.bundles.defaultStartLevel=4 -Declipse.product=org.eclipse.jdt.ls.core.product -Dlog.protocol=true -Dlog.level=ALL -Xms1g -Xmx2G -jar '$JDTLS_HOME'/plugins/org.eclipse.jdt.ls.core_*.jar -configuration '$JDTLS_HOME'/config_linux -data "${1:-/tmp/jdtls-workspace}" --add-modules=ALL-SYSTEM --add-opens java.base/java.util=ALL-UNNAMED --add-opens java.base/java.lang=ALL-UNNAMED' > /usr/local/bin/jdtls \
    && chmod +x /usr/local/bin/jdtls

# Additional language servers

# Bash Language Server
RUN npm install -g bash-language-server

# YAML Language Server
RUN npm install -g yaml-language-server

# JSON Language Server  
RUN npm install -g vscode-json-languageserver

# HTML/CSS Language Servers
RUN npm install -g vscode-html-languageserver-bin vscode-css-languageserver-bin

# Dockerfile Language Server
RUN npm install -g dockerfile-language-server-nodejs

# Install additional CLI linters/formatters

# Shellcheck (bash linting)
RUN apt-get update && apt-get install -y shellcheck && rm -rf /var/lib/apt/lists/*

# Prettier (formatting)
RUN npm install -g prettier

# Set up workspace
WORKDIR /workspace

# Create entrypoint script that can launch any language server
RUN cat > /usr/local/bin/lsp-launcher.sh << 'EOF'
#!/bin/bash

# LSP Server Launcher Script
# Usage: lsp-launcher.sh <server_type> [args...]

SERVER_TYPE="$1"
shift

case "$SERVER_TYPE" in
    "typescript")
        exec typescript-language-server --stdio "$@"
        ;;
    "python"|"pyright")
        exec pyright-langserver --stdio "$@"
        ;;
    "go"|"gopls")
        exec gopls "$@"
        ;;
    "rust"|"rust-analyzer")
        exec rust-analyzer "$@"
        ;;
    "ruby"|"ruby-lsp")
        exec ruby-lsp --stdio "$@"
        ;;
    "csharp"|"csharp-ls")
        exec csharp-ls "$@"
        ;;
    "java"|"jdtls")
        if [ -z "$1" ]; then
            WORKSPACE_DIR="/tmp/jdtls-workspace-$$"
        else
            WORKSPACE_DIR="$1"
            shift
        fi
        exec jdtls "$WORKSPACE_DIR" "$@"
        ;;
    "cpp"|"c"|"clangd")
        exec clangd --background-index "$@"
        ;;
    "bash")
        exec bash-language-server start "$@"
        ;;
    "yaml")
        exec yaml-language-server --stdio "$@"
        ;;
    "json")
        exec vscode-json-languageserver --stdio "$@"
        ;;
    "html")
        exec html-languageserver --stdio "$@"
        ;;
    "css")
        exec css-languageserver --stdio "$@"
        ;;
    "dockerfile")
        exec docker-langserver --stdio "$@"
        ;;
    *)
        echo "Unknown server type: $SERVER_TYPE" >&2
        echo "Available servers: typescript, python, go, rust, ruby, csharp, java, cpp, bash, yaml, json, html, css, dockerfile" >&2
        exit 1
        ;;
esac
EOF

RUN chmod +x /usr/local/bin/lsp-launcher.sh

# Create health check script
RUN cat > /usr/local/bin/health-check.sh << 'EOF'
#!/bin/bash
# Health check for LSP servers
echo "LSP container health check"
echo "Available language servers:"
echo "- TypeScript: $(which typescript-language-server)"
echo "- Python: $(which pyright-langserver)"
echo "- Go: $(which gopls)"
echo "- Rust: $(which rust-analyzer)"
echo "- Ruby: $(which ruby-lsp)"
echo "- C#: $(which csharp-ls)"
echo "- Java: $(ls $JDTLS_HOME/plugins/org.eclipse.jdt.ls.core_*.jar 2>/dev/null | head -1)"
echo "- C++: $(which clangd)"
echo "Container ready!"
EOF

RUN chmod +x /usr/local/bin/health-check.sh

# Set default entrypoint
ENTRYPOINT ["/usr/local/bin/lsp-launcher.sh"]

# Default to showing help
CMD ["help"]