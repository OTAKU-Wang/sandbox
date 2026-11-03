package vol_test

import . "cds-volume-plugin/internal/vol"

// A199b — consumer half of the A199 classification contract (server.go leg, go-02c
// handoff). plugin.go exports ErrInvalidVolumeID / ErrVolumeNotContained and wraps
// them with %w, but IsInputRejection keys on the message PREFIX, so the class only
// survives while every layer reproduces the producer's wording byte-for-byte: one
// contextual rewrap ("layer said: %w") flips a request that can never succeed from
// InvalidArgument to FailedPrecondition — the retryable-looking class this whole
// series exists to remove. Identity (errors.Is) becomes the classifier; the prefix
// table stays ONLY as the compatibility path for layers that still word rejections
// as text (rootfs.go id guards, layout.go:79) until they export sentinels too.

import (
	"errors"
	"fmt"
	"testing"

	"google.golang.org/grpc/codes"
	"google.golang.org/grpc/status"
)

func TestA199B_SentinelIdentitySurvivesContextRewrap(t *testing.T) {
	cases := []error{
		fmt.Errorf("rootfs said: %w", ErrInvalidVolumeID),
		fmt.Errorf("layout said: %w", ErrVolumeNotContained),
		fmt.Errorf("outer: %w", fmt.Errorf("inner: %w", ErrInvalidVolumeID)),
	}
	for _, err := range cases {
		if !IsInputRejection(err) {
			t.Errorf("%v is an A199 rejection by identity but the classifier missed it: "+
				"MapErr reports it to cubelet as a retryable-looking FailedPrecondition — "+
				"classification keyed on wording does not survive a contextual rewrap (A199)", err)
		}
		if got := status.Code(MapErr(err)); got != codes.InvalidArgument {
			t.Errorf("MapErr(%v) = %s, want INVALID_ARGUMENT", err, got)
		}
	}
}

func TestA199B_ProducedErrorsStillClassify(t *testing.T) {
	// The refactor must not move the existing classifications: whatever the real
	// producers emit (their text is byte-locked in plugin_test.go and the two
	// Python source-parsing gates) still reaches the client as INVALID_ARGUMENT.
	for _, in := range []error{
		ValidateVolumeID(""),
		ValidateVolumeID("a/b"),
		ValidateVolumeID("cds-data-*"),
		&VolumeNotContainedError{ID: "cds-data-z"},
	} {
		if !IsInputRejection(in) {
			t.Errorf("%v is no longer recognised as an input rejection", in)
		}
		if got := status.Code(MapErr(in)); got != codes.InvalidArgument {
			t.Errorf("MapErr(%v) = %s, want INVALID_ARGUMENT", in, got)
		}
	}
}

func TestA199B_CompatPrefixesStillClassifyExternalLayers(t *testing.T) {
	// Derived from the table itself (no copied literals): every registered stem
	// must still classify until rootfs.go/layout.go carry sentinels.
	for _, p := range InputRejectionPrefixes {
		err := fmt.Errorf("%s %q: wording from a layer without sentinels (fail-closed)", p, "x")
		if !IsInputRejection(err) {
			t.Errorf("registered rejection stem %q no longer classifies — removing the table "+
				"before those layers carry sentinels would regress their class (A199)", p)
		}
		if got := status.Code(MapErr(err)); got != codes.InvalidArgument {
			t.Errorf("MapErr(%v) = %s, want INVALID_ARGUMENT", err, got)
		}
	}
}

func TestA199B_OrdinaryStateErrorIsNotAnInputRejection(t *testing.T) {
	// The identity arm must not over-fire: state errors stay FailedPrecondition.
	for _, msg := range []string{
		"volume dir busy (fail-closed)",
		"manifest volume_id mismatch (fail-closed)",
	} {
		err := errors.New(msg)
		if IsInputRejection(err) {
			t.Errorf("%q was classed as an input rejection", msg)
		}
		if got := status.Code(MapErr(err)); got != codes.FailedPrecondition {
			t.Errorf("MapErr(%v) = %s, want FAILED_PRECONDITION", err, got)
		}
	}
}
