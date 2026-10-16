# ========================================================
# Stage 1: Build Next.js Frontend
# ========================================================
FROM node:20-alpine AS frontend-builder
WORKDIR /app/frontend

COPY frontend/package*.json ./
RUN npm ci

COPY frontend/ ./
ENV BACKEND_API_URL=http://127.0.0.1:8000
RUN npm run build

# ========================================================
# Stage 2: Final Production Runtime Container
# ========================================================
FROM python:3.11-slim AS runner
WORKDIR /app

# Install system dependencies (Node.js runtime, curl for healthchecks, ca-certificates for MCP SSL)
RUN apt-get update && apt-get install -y --no-install-recommends \
    nodejs \
    npm \
    curl \
    ca-certificates \
    && rm -rf /var/lib/apt/lists/*

# Install Python backend dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy Backend Source Code & Data
COPY src/ ./src/
RUN mkdir -p /app/data
COPY schema.sql ./schema.sql
COPY scripts/ ./scripts/
COPY scripts/start-render.sh /app/start.sh
RUN sed -i 's/\r$//' /app/start.sh && chmod +x /app/start.sh

# Copy MCP Servers & Precompiled Discovery Binaries
COPY mcp_servers/ ./mcp_servers/
RUN chmod +x /app/mcp_servers/openings-mcp/bin/* 2>/dev/null || true && \
    if [ -f /app/mcp_servers/openings-mcp/bin/openings-mcp-linux-amd64 ]; then \
      ln -sf /app/mcp_servers/openings-mcp/bin/openings-mcp-linux-amd64 /usr/local/bin/openings-mcp; \
    fi

# Copy Frontend Build Artifacts & Node Modules
WORKDIR /app/frontend
COPY --from=frontend-builder /app/frontend/package*.json ./
COPY --from=frontend-builder /app/frontend/.next ./.next
COPY --from=frontend-builder /app/frontend/public ./public
COPY --from=frontend-builder /app/frontend/node_modules ./node_modules
COPY --from=frontend-builder /app/frontend/next.config.ts ./

WORKDIR /app

# Render injects $PORT (defaults to 3000 locally)
EXPOSE 3000

HEALTHCHECK --interval=30s --timeout=5s --start-period=15s --retries=3 \
  CMD curl -f http://localhost:${PORT:-3000}/ || exit 1

ENTRYPOINT ["/app/start.sh"]
