FROM node:22-alpine AS build

RUN corepack enable
WORKDIR /workspace
COPY package.json pnpm-lock.yaml pnpm-workspace.yaml ./
COPY apps/web/package.json apps/web/package.json
RUN pnpm install --frozen-lockfile --filter @civitas/web...
COPY apps/web apps/web
RUN pnpm --filter @civitas/web build

FROM nginxinc/nginx-unprivileged:1.27-alpine

ENV CIVITAS_AUDIT_API_UPSTREAM=http://mcp-server:8001 \
    NGINX_ENVSUBST_FILTER=CIVITAS_AUDIT_API_UPSTREAM
COPY deploy/audit-viewer.nginx.conf /etc/nginx/templates/default.conf.template
COPY --from=build /workspace/apps/web/dist /usr/share/nginx/html
EXPOSE 8080
