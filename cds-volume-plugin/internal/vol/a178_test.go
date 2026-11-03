package vol_test

// A178 (OCR 2026-10-18, medium-low) — the helper timeout branch was
//
//	case <-time.After(...): _ = cmd.Process.Kill(); <-done; return err
//
// The Kill error is thrown away and `<-done` is unbounded. cmd.Wait() reaps the
// process and then blocks until every stdout/stderr COPY finishes, so a helper
// that leaves a descendant holding those pipes (a forked child, a shell `&`)
// means done never fires after the child is gone: the gRPC handler goroutine
// hangs forever on the very path that advertises a fail-closed timeout, and the
// log never says why.

import (
	"strings"
	"testing"
	"time"
)

// g02aOrphanHelper dies as soon as its stdin is written, but its backgrounded
// grandchild keeps the stdout/stderr pipe write ends open, which is exactly the
// state that makes cmd.Wait() return a reaped process and then block forever.
const g02aOrphanHelper = "sleep 20 &\ncat >/dev/null\n"

// TestA178_TimeoutBranchCannotHangTheCaller bounds the whole helper call: on a
// timeout the call must come back with an error that explains why the helper
// could not be stopped, in bounded time.
func TestA178_TimeoutBranchCannotHangTheCaller(t *testing.T) {
	helper := g02aHelperScript(t, g02aOrphanHelper)
	c, err := g02aTongsuo(t, helper, time.Second)
	if err != nil {
		t.Fatalf("NewCipher(tongsuo, 1s): %v", err)
	}

	res := make(chan error, 1)
	go func() {
		_, _, serr := c.Seal(g02aKey, g02aNonce, nil, g02aPlain)
		res <- serr
	}()

	select {
	case serr := <-res:
		if serr == nil {
			t.Fatal("a timed-out helper must not report success")
		}
		msg := strings.ToLower(serr.Error())
		if !strings.Contains(msg, "timeout") {
			t.Fatalf("expected a timeout error, got: %v", serr)
		}
		if !strings.Contains(msg, "kill") {
			t.Fatalf("the timeout error must disclose the helper-stop outcome — the Kill error and whether "+
				"the process was actually reaped — instead of dropping it, got: %v", serr)
		}
	case <-time.After(5 * time.Second):
		t.Fatal("HUNG: the timeout branch blocked forever on <-done. cmd.Wait() cannot return while a " +
			"descendant of the helper holds its stdout pipe, so the caller (a gRPC handler goroutine) is " +
			"pinned for the life of that process — the advertised fail-closed timeout guarantee is void (A178)")
	}
}

// TestA178_TimeoutBoundIsTheConfiguredBudget: the bounded reaping grace must stay
// tied to the configured timeout, not become a second unbounded wait.
func TestA178_TimeoutBoundIsTheConfiguredBudget(t *testing.T) {
	helper := g02aHelperScript(t, g02aOrphanHelper)
	c, err := g02aTongsuo(t, helper, time.Second)
	if err != nil {
		t.Fatalf("NewCipher(tongsuo, 1s): %v", err)
	}

	start := time.Now()
	res := make(chan error, 1)
	go func() {
		_, _, serr := c.Seal(g02aKey, g02aNonce, nil, g02aPlain)
		res <- serr
	}()

	select {
	case serr := <-res:
		el := time.Since(start)
		if serr == nil {
			t.Fatal("a timed-out helper must not report success")
		}
		if el > 5*time.Second {
			t.Fatalf("the timeout path took %s for a 1s budget: the reaping grace must be bounded by the "+
				"configured timeout, otherwise the fix just replaces a hang with a longer wait (A178)", el)
		}
		if el < time.Second {
			t.Fatalf("returned after %s, before the 1s helper budget expired — the timeout did not fire: %v", el, serr)
		}
	case <-time.After(6 * time.Second):
		t.Fatal("HUNG: timeout branch never returned (unbounded <-done) (A178)")
	}
}
