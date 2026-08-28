# Two images from one file. `dev` is what docker-compose runs: the Vite dev server with
# hot reload over a bind mount. The last stage is what a deploy gets: the built site,
# served static, single-page fallback — no dev server, no host allow-list, no proxy.

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

FROM node:22-alpine
WORKDIR /app
RUN npm install -g serve@14
COPY --from=build /app/dist ./dist
# shell form so $PORT expands: Railway sets it, compose does not
CMD serve -s dist -l ${PORT:-5163}
