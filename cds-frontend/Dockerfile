# CN-region / offline-cluster builds: both base images are overridable, e.g. the
# ddn mirror prefix (swr.cn-north-4.myhuaweicloud.com/ddn-k8s/<registry>/<repo>:<tag>):
#   --build-arg NODE_IMAGE=swr.cn-north-4.myhuaweicloud.com/ddn-k8s/docker.io/library/node:20-bookworm-slim
#   --build-arg NGINX_IMAGE=swr.cn-north-4.myhuaweicloud.com/ddn-k8s/docker.io/library/nginx:alpine
# Defaults are the upstream images, so CI and unrestricted networks are unchanged.
# The builder must be glibc, not musl: package-lock.json pins the glibc native
# binding of rolldown (Vite 8), so an alpine (musl) builder dies with
# "Cannot find module '@rolldown/binding-linux-x64-musl'" — verified by an actual
# build on 2026-10-16. The runtime stage stays alpine (only the builder is heavier).
ARG NODE_IMAGE=node:20-bookworm-slim
# Both base-image args must live in the GLOBAL scope (before the first FROM): an ARG
# line placed just before a later FROM belongs to the preceding stage, so the later
# FROM would see it as blank ("base name (${NGINX_IMAGE}) should not be blank").
ARG NGINX_IMAGE=nginx:alpine
FROM ${NODE_IMAGE} AS builder

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

FROM ${NGINX_IMAGE}
COPY --from=builder /app/dist /usr/share/nginx/html
COPY nginx.conf /etc/nginx/conf.d/default.conf
EXPOSE 80
CMD ["nginx", "-g", "daemon off;"]
