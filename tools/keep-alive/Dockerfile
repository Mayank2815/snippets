# Build stage — compiles the server and the UI.
FROM node:24-alpine AS build
WORKDIR /app

COPY package*.json ./
RUN npm ci
COPY ui/package*.json ./ui/
RUN npm ci --prefix ui

COPY tsconfig.json ./
COPY src ./src
COPY ui ./ui
RUN npm run build

# Runtime stage — production deps only, no toolchain, non-root.
FROM node:24-alpine AS runtime
WORKDIR /app
ENV NODE_ENV=production

# su-exec drops privileges in the entrypoint after fixing volume ownership.
RUN apk add --no-cache su-exec

COPY package*.json ./
RUN npm ci --omit=dev && npm cache clean --force

COPY --from=build /app/dist ./dist
COPY --from=build /app/ui/dist ./ui/dist

# The kept list lives here; mounted as a volume so it survives redeploys.
RUN mkdir -p /app/data && chown -R node:node /app

COPY docker-entrypoint.sh /usr/local/bin/docker-entrypoint.sh
RUN chmod +x /usr/local/bin/docker-entrypoint.sh

# No USER directive: the entrypoint starts as root only long enough to chown the
# mounted volume, then execs the app as node.
# 4311: task-notif has 4310 on the same VM; this is the next free port.
ENV PORT=4311 DATA_DIR=/app/data
EXPOSE 4311

# The ping loop is useless if the process is wedged, so check the API answers.
HEALTHCHECK --interval=60s --timeout=10s --start-period=15s --retries=3 \
  CMD node -e "fetch('http://127.0.0.1:'+(process.env.PORT||4311)+'/healthz').then(r=>process.exit(r.ok?0:1)).catch(()=>process.exit(1))"

ENTRYPOINT ["/usr/local/bin/docker-entrypoint.sh"]
CMD ["node", "dist/server.js"]
