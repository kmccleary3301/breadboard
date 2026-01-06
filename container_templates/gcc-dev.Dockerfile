# Simple C/C++ dev image
FROM debian:bookworm
RUN apt-get update && apt-get install -y build-essential cmake gdb git curl && rm -rf /var/lib/apt/lists/*
WORKDIR /workspace

