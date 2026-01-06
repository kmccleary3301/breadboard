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

## Usage

```bash
# Build a specific template
docker build -f gcc-dev.Dockerfile -t my-gcc-dev .

# Run interactively
docker run -it --rm -v $(pwd):/workspace my-gcc-dev

# Or use the build script
./build_all.sh
```