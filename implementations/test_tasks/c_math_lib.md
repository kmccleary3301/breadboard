Task: Implement a small C math library with a Makefile and run tests under GCC.

Requirements:
- Library `libcmath` providing functions:
  - `int gcd(int a, int b)` — Euclidean algorithm
  - `long long fib(int n)` — iterative; handle n>=0; guard overflow for large n (document behavior)
- Headers in `include/`, sources in `src/`, tests in `tests/`.
- Provide a `Makefile` with targets: `all`, `lib`, `test`, `clean`.
- Build a static library `libcmath.a` and a test binary `test_cmath`.
- Tests: cover typical and edge cases; exit non-zero on failure.
- Commands to run:
  - `make -j` builds library and tests
  - `./tests/test_cmath` runs tests
- Goal: Successful build and test run, with clean output.


