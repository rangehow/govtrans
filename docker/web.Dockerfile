# Stage 1: Build the frontend SPA
FROM node:20-alpine AS builder

ARG NPM_REGISTRY=https://registry.npmjs.org
WORKDIR /app

# Copy package files for dependency caching
COPY apps/web/package*.json ./

# Install dependencies with configurable registry
RUN npm config set registry ${NPM_REGISTRY} && \
    npm ci

# Copy frontend source code
COPY apps/web/ ./

# Build production assets (Vite build)
RUN npm run build

# Stage 2: Serve with Nginx
FROM nginx:alpine

# Copy built static assets from builder stage
COPY --from=builder /app/dist /usr/share/nginx/html

# Copy custom Nginx reverse proxy and SPA configuration
COPY docker/nginx.conf /etc/nginx/conf.d/default.conf

EXPOSE 80

CMD ["nginx", "-g", "daemon off;"]
