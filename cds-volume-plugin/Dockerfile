# cds-volume-plugin image (SS-10 CB-03 spec step 6).
#
# Build from the CDS repo root (context must include the vendored Tongsuo;
# run `scripts/prepare_tongsuo.sh` first — Tongsuo is a sha-pinned gitlink, the
# 406MB source is not in this repo):
#   docker build -f cds-volume-plugin/Dockerfile -t cds-volume-plugin:<ver> .
#
# Stages: Tongsuo (vendored source → libtongsuo) → static sm4_gcm helper
# → Go plugin → slim runtime. The helper is statically linked against
# libcrypto.a so the runtime stage needs no Tongsuo shared libraries.
#
# Base images are pinned to immutable digests (A205④ supply-chain traceability): the tag is
# kept for the human reader, the digest is what the build actually pulls, and README.md 构建节
# records when and how each digest was resolved. There is deliberately no `ARG GO_VER` knob
# any more — a --build-arg that rewrites the image name while the digest stays put silently
# un-pins the toolchain. To move the Go compiler, re-resolve the digest from the registry and
# change all three golang lines together; a205c_digest_pin_test.go reddens if they drift apart.
FROM golang:1.25-bookworm@sha256:3b4a11519ad929d1e1d261a12cff056f0c85b735253d7d861346b9c6f8b36437 AS tongsuo
RUN apt-get update && apt-get install -y --no-install-recommends \
        build-essential perl ca-certificates && rm -rf /var/lib/apt/lists/*
COPY Tongsuo/ /src/Tongsuo/
WORKDIR /src/Tongsuo
# `--libdir=lib64` is pinned (A204③): sm4gcm/Makefile hard-codes
#   TSLIB := $(abspath $(PREFIX)/lib64)
# while this stage used to probe the install with a lib* WILDCARD, which can pass while
# the helper stage fails with an opaque `cc: ... No such file`. One libdir, one口径,
# asserted by a204_build_gates_test.go against sm4gcm/Makefile.
RUN ./Configure --prefix=/tongsuo --libdir=lib64 no-tests \
 && make -j"$(nproc)" > /dev/null \
 && make install_sw \
 && ls /tongsuo/lib64/libcrypto.a /tongsuo/lib64/libcrypto.so

FROM golang:1.25-bookworm@sha256:3b4a11519ad929d1e1d261a12cff056f0c85b735253d7d861346b9c6f8b36437 AS helper
RUN apt-get update && apt-get install -y --no-install-recommends \
        gcc make libc6-dev && rm -rf /var/lib/apt/lists/*
COPY --from=tongsuo /tongsuo /tongsuo
COPY cds-volume-plugin/sm4gcm/ /src/sm4gcm/
WORKDIR /src/sm4gcm
RUN mkdir -p /out \
 && make PREFIX=/tongsuo sm4_gcm-static \
 && mv sm4_gcm-static /out/cds-sm4-gcm
RUN /out/cds-sm4-gcm frobnicate; test $? -eq 1 && echo helper-usage-ok

FROM golang:1.25-bookworm@sha256:3b4a11519ad929d1e1d261a12cff056f0c85b735253d7d861346b9c6f8b36437 AS plugin
WORKDIR /src
COPY cds-volume-plugin/go.mod cds-volume-plugin/go.sum ./
RUN go mod download
COPY cds-volume-plugin/*.go ./
COPY cds-volume-plugin/internal/ ./internal/
COPY cds-volume-plugin/gen/ ./gen/
COPY cds-volume-plugin/proto/ ./proto/
RUN CGO_ENABLED=0 go build -trimpath -ldflags='-s -w' -o /out/cds-volume-plugin .

FROM debian:bookworm-slim@sha256:7c7b2c966bc9ee8cedfeef67e0e279108992c77681fa595db4a9d65c06ccc587
# A204①: debian:bookworm-slim ships an EMPTY trust store (`ls /etc/ssl/certs/ca-certificates.crt`
# → No such file), so an https CDS_VOLUME_PLUGIN_KMS_BASE_URL passed every build gate and then
# failed EVERY DEK call with `x509: certificate signed by unknown authority` against a
# fail-closed KMS client with no offline mode. The boot gate in config.go (checkTrustRoots)
# only turns that into a refusal; the image is what has to carry the roots.
RUN apt-get update \
 && apt-get install -y --no-install-recommends ca-certificates \
 && rm -rf /var/lib/apt/lists/* \
 && test -s /etc/ssl/certs/ca-certificates.crt
# prod identity per spec §2.3: socket root:cds 0600, Cubelet runtime user
# is a member of the cds group.
RUN groupadd --system cds \
 && useradd --system --gid cds --no-create-home --shell /usr/sbin/nologin cubelet
COPY --from=plugin /out/cds-volume-plugin /usr/local/bin/cds-volume-plugin
COPY --from=helper /out/cds-sm4-gcm /usr/local/bin/cds-sm4-gcm
ENV CDS_SM4_AEAD_BACKEND=tongsuo \
    CDS_CUBE_VOLUME_PLUGIN_SOCKET=/run/cds-volume-plugin/cds.sock \
    CDS_VOLUME_PLUGIN_VOLUME_ROOT=/var/lib/cds/volumes \
    CDS_VOLUME_PLUGIN_SM4_HELPER=/usr/local/bin/cds-sm4-gcm
# KMS transport (A179) — this ENTRYPOINT ("serve") is the unit that starts the
# plugin, so the switch is set here by the operator, not in the image. The KMS
# leg carries DEK hex + the service bearer token, hence a non-https
# CDS_VOLUME_PLUGIN_KMS_BASE_URL refuses to boot unless the host is a loopback IP
# literal or CDS_VOLUME_PLUGIN_KMS_ALLOW_INSECURE=1 is set (boot WARNING either
# way). Production supplies the trust material:
#   CDS_VOLUME_PLUGIN_KMS_BASE_URL=https://<kms>:8443
#   CDS_VOLUME_PLUGIN_KMS_CA_FILE=/etc/cds/kms/ca.pem
#   CDS_VOLUME_PLUGIN_KMS_CLIENT_CERT=/etc/cds/kms/client.pem   (with _CLIENT_KEY)
# Mounting /etc/cds/kms read-only is the intended delivery; nothing here bakes a
# default that would weaken the gate.
# A175 image leg: the CONFIG DEFAULT for CDS_VOLUME_PLUGIN_SM4_HELPER is the bare name
# `sm4_gcm`, which exec.LookPath would resolve anywhere on PATH — and the helper is fed the
# plaintext DEK on stdin, so a PATH-writable directory hands DEKs to any binary (the boot
# gate belongs in config.go; see evidence NEEDS-OWNER). This image never relies on that
# default: the helper is pinned to an absolute path, and the pin is asserted at BUILD time
# so a renamed/mis-copied helper fails the build instead of the first Seal().
RUN test -x /usr/local/bin/cds-sm4-gcm \
 && test -x /usr/local/bin/cds-volume-plugin \
 && [ "$(printenv CDS_VOLUME_PLUGIN_SM4_HELPER)" = /usr/local/bin/cds-sm4-gcm ]
# The plugin MUST run as root: loop-mount/unmount of the sandbox rootfs and
# chown root:cds on the socket are both privileged (server.go enforceSocketAccess refuses
# to serve as non-root unless CDS_VOLUME_PLUGIN_ALLOW_NON_ROOT=1, A205①). Stated
# explicitly instead of left implicit in the base image.
USER root
VOLUME ["/var/lib/cds/volumes"]
ENTRYPOINT ["/usr/local/bin/cds-volume-plugin", "serve"]
