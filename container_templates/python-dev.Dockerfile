FROM python:3.12-slim
RUN apt-get update && apt-get install -y git build-essential ripgrep curl && rm -rf /var/lib/apt/lists/*
RUN pip install --no-cache-dir ipython rich pytest jupyter
WORKDIR /workspace
CMD ["bash"] 