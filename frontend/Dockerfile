# ponytail: dev-server image only; add a multi-stage build + nginx stage when you deploy.
FROM node:22-alpine
WORKDIR /app
COPY package.json .
RUN npm install
COPY . .
CMD ["npm", "run", "dev"]
