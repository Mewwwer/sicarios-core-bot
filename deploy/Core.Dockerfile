# syntax=docker/dockerfile:1
# Optional Core image; build context is the repository root.
# Existing working Northflank build configuration can also be retained.
FROM node:24-bookworm-slim
ENV NODE_ENV=production
WORKDIR /app
COPY --chown=node:node package.json package-lock.json ./
RUN --mount=type=secret,id=proxy_ca \
    if [ -f /run/secrets/proxy_ca ]; then export NODE_EXTRA_CA_CERTS=/run/secrets/proxy_ca; fi; \
    npm ci --omit=dev
COPY --chown=node:node src/ ./src/
USER node
EXPOSE 8080
CMD ["npm", "start"]
