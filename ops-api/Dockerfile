FROM node:20-alpine AS build
WORKDIR /app/ops-api
COPY ops-api/package*.json ./
RUN npm ci
COPY ops-api/ ./
RUN npm run build

FROM node:20-alpine
ENV NODE_ENV=production
WORKDIR /app/ops-api
COPY ops-api/package*.json ./
RUN npm ci --omit=dev && npm cache clean --force
COPY --from=build /app/ops-api/dist ./dist
COPY db /app/db
USER node
EXPOSE 3001
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
  CMD wget -qO- http://127.0.0.1:3001/health || exit 1
CMD ["node", "dist/server.js"]
