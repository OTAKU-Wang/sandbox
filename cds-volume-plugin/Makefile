# cds-volume-plugin build (SS-10 CB-03).
#
# make local  — build the sm4_gcm helper + plugin binary natively (dev path)
# make helper — build the C helper only
# make docker — build the plugin OCI image (helper + libtongsuo baked in)
#
# Vendored Tongsuo build (prerequisite for local, already done on this box):
#   cd ../Tongsuo
#   ./Configure --prefix=$PWD/cds-volume-plugin/tongsuo-build no-tests
#   make -j$(nproc) && make install_sw
# (standard make outputs inside the vendored tree only; no source edits)

TS_PREFIX := tongsuo-build
BIN       := cds-volume-plugin

.PHONY: local helper test docker clean

local: helper
	mkdir -p bin
	go build -o bin/$(BIN) .

helper:
	$(MAKE) -C sm4gcm PREFIX=$(abspath $(TS_PREFIX)) sm4_gcm
	mkdir -p bin
	cp -f sm4gcm/sm4_gcm bin/sm4_gcm

# `test` depends on `helper` (A204②): the GM negative paths in plugin_test.go and
# a204_test.go FAIL CLOSED when sm4gcm/sm4_gcm is missing, so on a clean checkout
# `make test` must build the helper first instead of reporting a green gate with
# zero national-crypto coverage. Raw `go test ./...` without the helper therefore
# fails by design — run `make test` (or `make helper` first).
test: helper
	go test ./...

docker:
	cd .. && docker build -f cds-volume-plugin/Dockerfile -t cds-volume-plugin:dev .

clean:
	rm -rf bin
	$(MAKE) -C sm4gcm clean
