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

test:
	go test ./...

docker:
	cd .. && docker build -f cds-volume-plugin/Dockerfile -t cds-volume-plugin:dev .

clean:
	rm -rf bin
	$(MAKE) -C sm4gcm clean
