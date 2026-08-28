# Two images from one file. `dev` is what docker-compose runs: the Vite dev server with
# hot reload over a bind mount. The last stage is what a deploy gets: the built site
# served by Caddy, which also proxies /api to the backend (see Caddyfile) — so the API
# needs no public address and the browser sees one origin.

FROM node:22-alpine AS dev
WORKDIR /app
COPY package.json package-lock.json ./
RUN npm ci
COPY . .
CMD ["npm", "run", "dev"]

FROM dev AS build
# VITE_* values are baked into the site here, not read at runtime. Railway hands a
# Dockerfile build its service variables as build args — but only the ones declared,
# so every variable the site is built with is named here.
ARG VITE_API_URL
ARG VITE_AUTH_PROVIDER
ARG VITE_AUTH_ISSUER
ARG VITE_AUTH_CLIENT_ID
ARG VITE_AUTH_REDIRECT_URI
ARG VITE_AUTH_LOGOUT_URI
RUN npm run build

FROM caddy:2-alpine
COPY --from=build /app/dist /srv
COPY Caddyfile /etc/caddy/Caddyfile
