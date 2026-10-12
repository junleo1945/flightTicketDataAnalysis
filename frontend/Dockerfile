# 构建阶段
FROM node:22-alpine AS build
ARG NPM_REGISTRY=https://registry.npmmirror.com
WORKDIR /app
RUN npm config set registry ${NPM_REGISTRY}
COPY package.json package-lock.json ./
RUN npm install
COPY . .
RUN npm run build

# 运行阶段：nginx 托管静态文件并转发 /api
FROM nginx:1.27-alpine
COPY nginx.conf /etc/nginx/conf.d/default.conf
COPY --from=build /app/dist /usr/share/nginx/html
EXPOSE 80
