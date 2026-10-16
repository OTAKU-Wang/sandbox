# sm4_gcm helper build — links the vendored-Tongsuo libtongsuo (SS-10 CB-03 §2.9).
#
# Tongsuo build commands actually run on this machine (2026-10-13):
#   cd ../../Tongsuo
#   ./Configure --prefix=$PWD/../cds-volume-plugin/tongsuo-build no-tests
#   make -j12 && make install_sw
# (build artifacts inside the vendored tree are the standard make outputs only)
#
# The Tongsuo install puts shared libs in <prefix>/lib64.

PREFIX ?= ../tongsuo-build
TSLIB  := $(abspath $(PREFIX)/lib64)
TSINC  := $(abspath $(PREFIX)/include)
CC     ?= cc
CFLAGS ?= -O2 -Wall -Wextra

BIN := sm4_gcm

all: $(BIN)

$(BIN): sm4_gcm.c sm4_gcm.h
	$(CC) $(CFLAGS) -I$(TSINC) -o $@ sm4_gcm.c -L$(TSLIB) -lcrypto -Wl,-rpath,$(TSLIB)

# static-variant for image baking (no rpath dependency)
$(BIN)-static: sm4_gcm.c sm4_gcm.h
	$(CC) $(CFLAGS) -I$(TSINC) -o $@ sm4_gcm.c $(TSLIB)/libcrypto.a -ldl -pthread

clean:
	rm -f $(BIN) $(BIN)-static

.PHONY: all clean
