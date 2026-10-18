#!/usr/bin/env bash
set -euo pipefail
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

die() { say "FAIL: $*"; exit 1; }

say() { printf '%s\n' "$*"; }

# URL 打印脱敏：带凭据的镜像地址（https://user:tok@host/...）绝不整体回显/落日志
mask_url() { printf '%s' "$1" | sed -E 's#(://[^/@]*@)#://***@#'; }

# 传输参数注入防线：
#  - `ext::` / `config::` 等 `*::` 是 git 特殊传输（ext:: 可执行任意命令，已知 RCE 向量）
#  - 以 `-` 开头的 URL/路径会被 git/tar 当选项解析（--upload-pack=... 直接执行）
# 裸本地路径是合法镜像源（测试与离线内网都这么用），不能按 scheme 白名单一刀切。
validate_url() {
  case "$1" in
    -*|*::*) die "拒绝不安全的 TONGSUO_GIT_URL: $(mask_url "$1")（禁 ext::/::类传输与选项注入）" ;;
  esac
}

sha256_of() {
  if command -v sha256sum >/dev/null 2>&1; then
    sha256sum -- "$1" | awk '{print $1}'
  else
    shasum -a 256 -- "$1" | awk '{print $1}'
  fi
}

# gitlink 必须真实存在且是 40 位 hex：mode!=160000（普通文件/目录占位）时拿到的
# "PIN" 不是 commit sha，后续一切比对都无意义
GITLINK_LINE=$(git -C "$REPO" ls-files -s Tongsuo | head -1)
PIN=$(printf '%s' "$GITLINK_LINE" | awk '$1=="160000"{print $2}')
if [ -z "$PIN" ]; then
  echo "FAIL: 仓内没有 Tongsuo gitlink（git ls-files -s Tongsuo 为空或非 160000 gitlink）" >&2
  exit 1
fi
if ! printf '%s' "$PIN" | grep -Eq '^[0-9a-f]{40}$'; then
  echo "FAIL: gitlink sha 非法（期望 40 位 hex）: $PIN" >&2
  exit 1
fi

have_src() {
  [ -f "$1/Configure" ] && [ -f "$1/crypto/sm4/sm4.c" ] && [ -f "$1/include/openssl/opensslv.h.in" ]
}

# 已就绪短路：git 树比对 HEAD；tarball 树（无 git 元数据）比对落地时写的 .tongsuo-pin 标记
if [ -d "$DEST" ] && have_src "$DEST"; then
  if [ "$(git -C "$DEST" rev-parse HEAD 2>/dev/null || true)" = "$PIN" ]; then
    say "READY path=$DEST sha=$PIN (已是钉住版本)"
    exit 0
  fi
  if [ ! -d "$DEST/.git" ] && [ "$(cat "$DEST/.tongsuo-pin" 2>/dev/null || true)" = "$PIN" ]; then
    say "READY path=$DEST sha=$PIN (源包落地，.tongsuo-pin 标记吻合)"
    exit 0
  fi
fi

if [ -n "${TONGSUO_TARBALL:-}" ]; then
  case "$TONGSUO_TARBALL" in
    -*) die "TONGSUO_TARBALL 路径不得以 - 开头: $TONGSUO_TARBALL" ;;
  esac
  if [ ! -f "$TONGSUO_TARBALL" ]; then
    say "FAIL: 源包不存在: $TONGSUO_TARBALL"
    exit 1
  fi
  got=$(sha256_of "$TONGSUO_TARBALL")
  if [ "$got" != "$TONGSUO_TARBALL_SHA256" ]; then
    say "FAIL: 源包 sha256 不匹配"
    say "  got  =$got"
    say "  want =$TONGSUO_TARBALL_SHA256"
    exit 1
  fi
  # 解包到唯一临时目录（并发/重试不互踩），再校验布局，最后原子换入
  TMPD=$(mktemp -d "${DEST}.tar.XXXXXX")
  if ! tar -xzf "$TONGSUO_TARBALL" -C "$TMPD"; then
    rm -rf "$TMPD"; say "FAIL: 解包失败"; exit 1
  fi
  # git archive 常见顶层多一层 tongsuo-<ref>/ 目录
  if ! have_src "$TMPD"; then
    top=$(find "$TMPD" -mindepth 1 -maxdepth 1 -type d | head -1)
    if [ -n "$top" ] && have_src "$top"; then
      SRC_DIR="$top"
    else
      rm -rf "$TMPD"
      say "FAIL: 解包后缺 Configure/crypto/sm4/sm4.c/include/openssl/opensslv.h.in"
      exit 1
    fi
  else
    SRC_DIR="$TMPD"
  fi
  # pin 标记：声明"这份树对应仓内钉住 sha"，无标记的裸目录不许走 READY 短路
  printf '%s\n' "$PIN" > "$SRC_DIR/.tongsuo-pin"
  rm -rf "$DEST.old"
  if [ -d "$DEST" ]; then mv "$DEST" "$DEST.old"; fi
  if ! mv "$SRC_DIR" "$DEST"; then
    if [ -d "$DEST.old" ]; then mv "$DEST.old" "$DEST"; fi
    rm -rf "$TMPD"
    die "源包换入失败，已回滚到原目录"
  fi
  rm -rf "$TMPD" "$DEST.old"
  say "READY path=$DEST sha256=$got (源包=$PIN 的 git archive，无 git 元数据，标记 .tongsuo-pin)"
  exit 0
fi

if [ -n "${TONGSUO_GIT_URL:-}" ]; then
  validate_url "$TONGSUO_GIT_URL"
  TMPD=$(mktemp -d "${DEST}.git.XXXXXX")
  rmdir "$TMPD"   # git clone 要求目标不存在
  if ! git clone --quiet -- "$TONGSUO_GIT_URL" "$TMPD"; then
    rm -rf "$TMPD"
    say "FAIL: 镜像 clone 失败 $(mask_url "$TONGSUO_GIT_URL")"
    exit 1
  fi
  if ! git -C "$TMPD" cat-file -e "${PIN}^{commit}" 2>/dev/null; then
    say "FAIL: 镜像里没有钉住 sha $PIN（请先 mirror 上游 tag/branch）"
    rm -rf "$TMPD"; exit 1
  fi
  if ! git -C "$TMPD" checkout --quiet --detach "$PIN"; then
    say "FAIL: checkout $PIN 失败"
    rm -rf "$TMPD"; exit 1
  fi
  rm -rf "$DEST.old"
  if [ -d "$DEST" ]; then mv "$DEST" "$DEST.old"; fi
  if ! mv "$TMPD" "$DEST"; then
    if [ -d "$DEST.old" ]; then mv "$DEST.old" "$DEST"; fi
    rm -rf "$TMPD"
    die "镜像换入失败，已回滚到原目录"
  fi
  rm -rf "$DEST.old"
  say "READY path=$DEST sha=$PIN (内网镜像 $(mask_url "$TONGSUO_GIT_URL"))"
  exit 0
fi

say "FETCH: git submodule update --init --depth 1 Tongsuo ($UPSTREAM)"
# 浅取失败或浅取拿不到钉住 sha（shallow 边界/graft），都要退回完整 clone 再试一次，
# 而不是直接放弃——sha 不匹配与 rc 非零在这里是同一类"浅取不够"的失败。
if ! git -C "$REPO" submodule update --init --depth 1 Tongsuo \
   || [ "$(git -C "$DEST" rev-parse HEAD 2>/dev/null || true)" != "$PIN" ]; then
  say "WARN: shallow 取用失败或 sha 不吻合，退回完整 clone（约 400MB）"
  if ! git -C "$REPO" submodule update --init --force Tongsuo; then
    say "FAIL: 三条取用路径都不通。离线请用内网源包："
    say "  TONGSUO_TARBALL=/root/thirdparty/tongsuo-$PIN.tar.gz scripts/prepare_tongsuo.sh"
    say "  或 TONGSUO_GIT_URL=<内网 git 镜像> scripts/prepare_tongsuo.sh"
    exit 1
  fi
fi
[ "$(git -C "$DEST" rev-parse HEAD 2>/dev/null || true)" = "$PIN" ] || { say "FAIL: submodule 拿到的不是钉住 sha $PIN"; exit 1; }
have_src "$DEST" || { say "FAIL: submodule 内容不完整"; exit 1; }
say "READY path=$DEST sha=$PIN (submodule)"
