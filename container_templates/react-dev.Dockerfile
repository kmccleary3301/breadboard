FROM node:20-bullseye
RUN apt-get update && apt-get install -y git ripgrep curl && rm -rf /var/lib/apt/lists/*
RUN npm install -g yarn create-vite pnpm
WORKDIR /workspace
CMD ["bash"] 