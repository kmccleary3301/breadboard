# Container Templates

Development environment containers for different programming languages and frameworks.

## Templates

### gcc-dev.Dockerfile
C/C++ development environment with:
- Debian Bookworm base
- GCC, build-essential, cmake
- GDB debugger
- Git and curl

### python-dev.Dockerfile  
Python development environment with:
- Python 3.12 slim
- IPython, Rich, Pytest, Jupyter
- Build tools and ripgrep
- Git and curl

### react-dev.Dockerfile
Node.js/React development environment with:
- Node.js 20
- Yarn, pnpm, create-vite
- Ripgrep for fast searching
- Git and curl

Note: the React image intentionally installs global npm tooling with `--force`
because current `node:20-bullseye` images may already provide Yarn shims.

## Usage

```bash
# Build a specific template
docker build -f gcc-dev.Dockerfile -t my-gcc-dev .

# Run interactively
docker run -it --rm -v $(pwd):/workspace my-gcc-dev

# Or use the build script
./build_all.sh
```

## ZHL SWE Eval Usage

For ZHL/ZAYA route-smoke and future SWE-style private evals, use:

- `python-dev:latest` for Python, mixed core-harness, evidence, and most private SWE repair tasks.
- `react-dev:latest` for TypeScript/JavaScript/frontend tasks.
- `gcc-dev:latest` for C/C++/CMake tasks.

Preferred untrusted-code isolation is Docker with gVisor:

```bash
docker run --rm --runtime runsc --network none -v "$PWD:/workspace" -w /workspace python-dev:latest python --version
```

BreadBoard's Python sandbox driver uses `driver="docker"` plus `driver_options={"runtime": "runsc", "network": "none"}` or `RAY_DOCKER_RUNTIME=runsc`.
