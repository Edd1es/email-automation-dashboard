FROM node:22-slim AS build
WORKDIR /repo
COPY package.json package-lock.json ./
COPY gateway/package.json gateway/
COPY frontend/package.json frontend/
RUN npm ci
COPY frontend/ frontend/
RUN npm run build -w frontend

FROM node:22-slim
WORKDIR /repo
ENV NODE_ENV=production
COPY package.json package-lock.json ./
COPY gateway/package.json gateway/
COPY frontend/package.json frontend/
RUN npm ci --omit=dev -w gateway
COPY gateway/src gateway/src
COPY --from=build /repo/frontend/dist /repo/frontend/dist
ENV STATIC_DIR=/repo/frontend/dist
USER node
EXPOSE 4000
CMD ["node", "gateway/src/server.js"]
