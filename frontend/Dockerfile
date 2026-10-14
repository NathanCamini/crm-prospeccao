FROM node:22-alpine AS build
WORKDIR /web
COPY package.json package-lock.json ./
RUN npm ci --no-audit --no-fund
COPY tsconfig.json vite.config.ts index.html ./
COPY src ./src
RUN npm run build

# nginx sem privilégios de root, escutando na 8080
FROM nginxinc/nginx-unprivileged:1.27-alpine
# Modelo: o envsubst da imagem grava /etc/nginx/conf.d/default.conf na subida, trocando só CRM_IP_CADDY.
ENV CRM_IP_CADDY=172.31.247.2
COPY nginx.conf /etc/nginx/templates/default.conf.template
COPY --from=build /web/dist /usr/share/nginx/html
EXPOSE 8080
HEALTHCHECK --interval=30s --timeout=5s CMD wget -qO- http://127.0.0.1:8080/ >/dev/null || exit 1
