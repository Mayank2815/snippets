# The workbench is a static page plus an nginx reverse proxy; nothing to build.
FROM nginx:1.27-alpine
COPY nginx.conf /etc/nginx/conf.d/default.conf
COPY index.html /usr/share/nginx/html/index.html
EXPOSE 80
# Once a minute is plenty for a static proxy; 5 s covers a busy VM; three misses before
# unhealthy so one slow probe does not restart it. BusyBox wget ships in the alpine image.
HEALTHCHECK --interval=60s --timeout=5s --start-period=5s --retries=3 \
  CMD wget -qO- http://127.0.0.1/healthz >/dev/null || exit 1
