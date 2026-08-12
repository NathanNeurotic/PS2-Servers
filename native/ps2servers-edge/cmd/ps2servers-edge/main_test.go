package main

import (
	"testing"
	"time"
)

// --dump-schema marks metrics_period applies_when=metrics; this pins the
// binary behaviour that metadata describes, so the two cannot drift apart.
func TestParseMetricsPeriodOnlyAppliesWhenMetricsAreOn(t *testing.T) {
	// Off: the period is never parsed, so even garbage is accepted.
	for _, period := range []string{"banana", "", "999h"} {
		if d, err := parseMetricsPeriod(false, period); err != nil || d != 0 {
			t.Errorf("metrics off, period %q: got (%v, %v), want (0, nil)", period, d, err)
		}
	}
	// On: parsed and bounded to 1s..24h.
	for _, period := range []string{"banana", "500ms", "25h"} {
		if _, err := parseMetricsPeriod(true, period); err == nil {
			t.Errorf("metrics on, period %q: accepted, want rejection", period)
		}
	}
	if d, err := parseMetricsPeriod(true, "30s"); err != nil || d != 30*time.Second {
		t.Errorf("metrics on, period 30s: got (%v, %v), want (30s, nil)", d, err)
	}
}
