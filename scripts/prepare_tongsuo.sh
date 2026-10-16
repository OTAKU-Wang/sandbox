#!/usr/bin/env bash
set -uo pipefail
# 准备 vendored Tongsuo 源码：cds-volume-plugin 镜像（Dockerfile 的 tongsuo 阶段
# COPY Tongsuo/ 编 libcrypto.a）与国密 AEAD/TLCP 生产路径的构建前置。
#
# 仓内只登记钉住 sha 的 gitlink，406MB 源码本体不入库，因此 clone 后此目录为空。
# 取用优先级（任一成功即退出，全部失败则 fail-closed，绝不用"手边随便一个版本"顶上）：
#   1) TONGSUO_TARBALL=<path>  内网/离线源包；sha256 必须等于下方 TONGSUO_TARBALL_SHA256
#   2) TONGSUO_GIT_URL=<url>   内网 git 镜像，checkout 到钉住 sha
#   3) git submodule update --init   上游 .gitmodules 里的 https 地址

TONGSUO_TARBALL_SHA256=5b8483b14d6abcb5023bbd98c19796c7d03fcc827f565eb8f9390ad3d821005f
UPSTREAM=https://github.com/Tongsuo-Project/Tongsuo.git

REPO=$(cd "$(dirname "$0")/.." && pwd)
DEST="$REPO/Tongsuo"
PIN=$(git -C "$REPO" ls-files -s Tongsuo | awk '{print $2}')

if [ -z "$PIN" ]; then
  echo "FAIL: 仓内没有 Tongsuo gitlink（git ls-files -s Tongsuo 为空）" >&2
  exit 1
fi

say() { printf '%s\n' "$*"; }

have_src() {
  [ -f "$1/Configure" ] && [ -f "$1/crypto/sm4/sm4.c" ] && [ -f "$1/include/openssl/opensslv.h.in" ]
}

if [ -d "$DEST" ] && have_src "$DEST" && [ "$(git -C "$DEST" rev-parse HEAD 2>/dev/null)" = "$PIN" ]; then
  say "READY path=$DEST sha=$PIN (已是钉住版本)"
  exit 0
fi

if [ -n "${TONGSUO_TARBALL:-}" ]; then
  if [ ! -f "$TONGSUO_TARBALL" ]; then
    say "FAIL: 源包不存在: $TONGSUO_TARBALL"
    exit 1
  fi
  got=$(sha256sum "$TONGSUO_TARBALL" | awk '{print $1}')
  if [ "$got" != "$TONGSUO_TARBALL_SHA256" ]; then
    say "FAIL: 源包 sha256 不匹配"
    say "  got  =$got"
    say "  want =$TONGSUO_TARBALL_SHA256"
    exit 1
  fi
  rm -rf "$DEST"
  mkdir -p "$DEST"
  tar -xzf "$TONGSUO_TARBALL" -C "$DEST" || { say "FAIL: 解包失败"; exit 1; }
  have_src "$DEST" || { say "FAIL: 解包后缺 Configure/crypto/sm4/sm4.c/include/openssl/opensslv.h.in"; exit 1; }
  say "READY path=$DEST sha256=$got (源包=$PIN 的 git archive，无 git 元数据)"
  exit 0
fi

if [ -n "${TONGSUO_GIT_URL:-}" ]; then
  rm -rf "$DEST.tmp"
  git clone --quiet "$TONGSUO_GIT_URL" "$DEST.tmp" || { say "FAIL: 镜像 clone 失败 $TONGSUO_GIT_URL"; rm -rf "$DEST.tmp"; exit 1; }
  git -C "$DEST.tmp" cat-file -e "$PIN^{commit}" 2>/dev/null || {
    say "FAIL: 镜像里没有钉住 sha $PIN（请先 mirror 上游 tag/branch）"
    rm -rf "$DEST.tmp"; exit 1; }
  git -C "$DEST.tmp" checkout --quiet --detach "$PIN" || { say "FAIL: checkout $PIN 失败"; rm -rf "$DEST.tmp"; exit 1; }
  rm -rf "$DEST"
  mv "$DEST.tmp" "$DEST"
  say "READY path=$DEST sha=$PIN (内网镜像 $TONGSUO_GIT_URL)"
  exit 0
fi

say "FETCH: git submodule update --init --depth 1 Tongsuo ($UPSTREAM)"
if ! git -C "$REPO" submodule update --init --depth 1 Tongsuo; then
  say "WARN: shallow 取用失败，退回完整 clone（约 400MB）"
  git -C "$REPO" submodule update --init Tongsuo || {
    say "FAIL: 三条取用路径都不通。离线请用内网源包："
    say "  TONGSUO_TARBALL=/root/thirdparty/tongsuo-$PIN.tar.gz scripts/prepare_tongsuo.sh"
    say "  或 TONGSUO_GIT_URL=<内网 git 镜像> scripts/prepare_tongsuo.sh"
    exit 1; }
fi
[ "$(git -C "$DEST" rev-parse HEAD 2>/dev/null)" = "$PIN" ] || { say "FAIL: submodule 拿到的不是钉住 sha $PIN"; exit 1; }
have_src "$DEST" || { say "FAIL: submodule 内容不完整"; exit 1; }
say "READY path=$DEST sha=$PIN (submodule)"
