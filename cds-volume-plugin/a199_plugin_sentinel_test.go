package main

// Producer half of the A199 classification contract (go-02b handoff). The gRPC layer
// mapped these two rejection classes by message prefix because plugin.go exposed no
// typed error. The sentinels are now the machine contract; the interim prefix table in
// server.go must still match the producer text until server.go is switched to errors.Is
// (NEEDS-OWNER — that file is a closed face this round). Both halves are asserted here:
// a reworded message or a dropped wrap reddens this gate instead of silently turning a
// bad request into a retryable-looking FailedPrecondition.

import (
	"errors"
	"testing"

	"google.golang.org/grpc/codes"
	"google.golang.org/grpc/status"
)

func TestA199C_VolumeIDRejectionsCarryTheSentinel(t *testing.T) {
	hosts := []struct {
		name, id string
	}{
		{"empty", ""},
		{"dot-dot", "a..b"},
		{"pure traversal", ".."},
		{"separator", "a/b"},
		{"glob star", "cds-data-*"},
		{"newline forge", "a\nb"},
	}
	for _, h := range hosts {
		err := validateVolumeID(h.id)
		if err == nil {
			t.Fatalf("%s: validateVolumeID accepted %q", h.name, h.id)
		}
		if !errors.Is(err, ErrInvalidVolumeID) {
			t.Errorf("%s: %v is not errors.Is()-able against ErrInvalidVolumeID — server.go would "+
				"have to fall back to message matching", h.name, err)
		}
		if errors.Is(err, ErrVolumeNotContained) {
			t.Errorf("%s: bad shape must not be reported as a containment failure", h.name)
		}
	}
	if err := validateVolumeID("cds-data-0f1e2d3c4b5a6978"); err != nil {
		t.Fatalf("over-tightening guard: the id shape the control plane actually sends was refused: %v", err)
	}
}

// The message is the interim classifier's only key (server.go inputRejectionPrefixes and
// the a199 mapErr table), so the sentinel refactor must not move a single byte.
func TestA199C_SentinelWrapKeepsThePinnedText(t *testing.T) {
	cases := []struct {
		got, want string
	}{
		{validateVolumeID("").Error(), "invalid volume_id: 0 byte(s), want 1-200 (fail-closed)"},
		{validateVolumeID("a..b").Error(), `invalid volume_id "a..b": must be one path segment that does not traverse (fail-closed)`},
		{validateVolumeID("cds-data-*").Error(), "invalid volume_id: byte 9 is 0x2a, outside [A-Za-z0-9._-] (fail-closed)"},
		{(&volumeNotContainedError{id: "x"}).Error(), `volume_id "x" does not resolve to one directory under the volume root (fail-closed)`},
	}
	for _, c := range cases {
		if c.got != c.want {
			t.Errorf("classification text drifted (server.go matches on it):\n got %q\nwant %q", c.got, c.want)
		}
	}
}

func TestA199C_ContainmentSentinelIsItsOwnClass(t *testing.T) {
	e := &volumeNotContainedError{id: "cds-data-z"}
	if !errors.Is(e, ErrVolumeNotContained) {
		t.Fatal("containment error is not errors.Is()-able against ErrVolumeNotContained")
	}
	if errors.Is(e, ErrInvalidVolumeID) {
		t.Fatal("a containment failure must not be classed as a malformed id")
	}
	if errors.Is(ErrVolumeNotContained, ErrInvalidVolumeID) {
		t.Fatal("the two classes must stay distinguishable for the transport layer")
	}
}

// The regression guard for the refactor itself: whatever the classifier is (today: text
// prefixes), both classes must still reach the client as INVALID_ARGUMENT.
func TestA199C_BothClassesStillMapToInvalidArgument(t *testing.T) {
	inputs := []error{
		validateVolumeID(""),
		validateVolumeID("a/b"),
		validateVolumeID("cds-data-*"),
		&volumeNotContainedError{id: "cds-data-z"},
	}
	for _, in := range inputs {
		if !isInputRejection(in) {
			t.Errorf("%v is no longer recognised as an input rejection", in)
		}
		if got := status.Code(mapErr(in)); got != codes.InvalidArgument {
			t.Errorf("mapErr(%v) = %s, want INVALID_ARGUMENT", in, got)
		}
	}
}
