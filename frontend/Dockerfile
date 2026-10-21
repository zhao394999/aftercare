FROM node:26-bookworm-slim AS build
WORKDIR /app/frontend
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci
COPY frontend ./
RUN npm run build
FROM caddy:2-alpine
COPY --from=build /app/frontend/dist /srv
COPY deployment/Caddyfile /etc/caddy/Caddyfile
EXPOSE 80 443
