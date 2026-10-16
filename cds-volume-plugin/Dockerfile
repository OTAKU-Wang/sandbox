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
ARG GO_VER=1.25

FROM golang:${GO_VER}-bookworm AS tongsuo
RUN apt-get update && apt-get install -y --no-install-recommends \
        build-essential perl ca-certificates && rm -rf /var/lib/apt/lists/*
COPY Tongsuo/ /src/Tongsuo/
WORKDIR /src/Tongsuo
RUN ./Configure --prefix=/tongsuo no-tests \
 && make -j"$(nproc)" > /dev/null \
 && make install_sw \
 && ls /tongsuo/lib*/libcrypto.a

FROM golang:${GO_VER}-bookworm AS helper
RUN apt-get update && apt-get install -y --no-install-recommends \
        gcc make libc6-dev && rm -rf /var/lib/apt/lists/*
COPY --from=tongsuo /tongsuo /tongsuo
COPY cds-volume-plugin/sm4gcm/ /src/sm4gcm/
WORKDIR /src/sm4gcm
RUN mkdir -p /out \
 && make PREFIX=/tongsuo sm4_gcm-static \
 && mv sm4_gcm-static /out/cds-sm4-gcm
RUN /out/cds-sm4-gcm frobnicate; test $? -eq 1 && echo helper-usage-ok

FROM golang:${GO_VER}-bookworm AS plugin
WORKDIR /src
COPY cds-volume-plugin/go.mod cds-volume-plugin/go.sum ./
RUN go mod download
COPY cds-volume-plugin/*.go ./
COPY cds-volume-plugin/gen/ ./gen/
COPY cds-volume-plugin/proto/ ./proto/
RUN CGO_ENABLED=0 go build -trimpath -ldflags='-s -w' -o /out/cds-volume-plugin .

FROM debian:bookworm-slim
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
VOLUME ["/var/lib/cds/volumes"]
ENTRYPOINT ["/usr/local/bin/cds-volume-plugin", "serve"]
