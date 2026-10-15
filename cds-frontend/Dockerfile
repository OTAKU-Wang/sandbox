FROM node:20-alpine AS builder

# CN-region builds: pass --build-arg NPM_REGISTRY=https://registry.npmmirror.com
# (slow default-registry fetches trip npm's "Exit handler never called" race,
# which aborts the install mid-flight yet reports the step DONE).
ARG NPM_REGISTRY=https://registry.npmjs.org/

WORKDIR /app
COPY package*.json ./
# The build host cannot reliably resolve registry.npmjs.org (EAI_AGAIN) —
# every npm command in the build must use one reachable registry (CN builds:
# npmmirror). The floating node:20-alpine npm surfaces the DNS failure as the
# "Exit handler never called" race, which aborts `npm ci` mid-install yet
# exits 0 (node_modules incomplete -> tsc missing). Pin a stable npm and
# verify the toolchain actually landed before building.
RUN npm config set registry ${NPM_REGISTRY} \
    && npm install -g npm@10.8.2 \
    && for i in 1 2 3; do npm ci --no-audit --no-fund || rm -rf node_modules; done \
    && test -x node_modules/.bin/tsc && test -x node_modules/.bin/vite
COPY . .
RUN npm run build

FROM nginx:alpine
COPY --from=builder /app/dist /usr/share/nginx/html
COPY nginx.conf /etc/nginx/conf.d/default.conf
EXPOSE 80
CMD ["nginx", "-g", "daemon off;"]
