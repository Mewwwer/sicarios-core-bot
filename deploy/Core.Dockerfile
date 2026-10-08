# Optional Core image; build context is the repository root.
# Existing working Northflank build configuration can also be retained.
FROM node:24-bookworm-slim
ENV NODE_ENV=production
WORKDIR /app
COPY --chown=node:node package.json package-lock.json ./
RUN npm ci --omit=dev
COPY --chown=node:node src/ ./src/
USER node
EXPOSE 8080
CMD ["npm", "start"]
