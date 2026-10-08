package main

import (
	"testing"
	"time"
)

func TestRuncStatsUnitsAndMissingFields(t *testing.T) {
	frame := []byte(`{"type":"stats","data":{"cpu":{"usage":{"total":2500000000}},"memory":{"usage":{"usage":67108864}}}}`)
	previous := Metrics{total: 1000000000, sample: testNow}
	m := parseRuncStats(frame, previous, testNow.Add(2*time.Second))
	if !m.Valid || m.CPUUsageMillis != 750 || m.MemoryUsageBytes != 64<<20 {
		t.Fatalf("incorrect stats units: %+v", m)
	}
	if parseRuncStats(frame, Metrics{}, testNow).Valid {
		t.Fatal("first CPU sample incorrectly considered valid")
	}
	for _, bad := range []string{`{}`, `{"type":"stats","data":{}}`, `{"type":"exit"}`, `invalid`} {
		if parseRuncStats([]byte(bad), previous, testNow.Add(time.Second)).Valid {
			t.Fatal("missing stats fields used to scale down")
		}
	}
}
