package main

import (
	"encoding/json"
	"math"
	"time"
)

func parseRuncStats(out []byte, previous Metrics, now time.Time) Metrics {
	var result struct {
		Type string `json:"type"`
		Data struct {
			CPU struct {
				Usage struct {
					Total *uint64 `json:"total"`
				} `json:"usage"`
			} `json:"cpu"`
			Memory struct {
				Usage struct {
					Usage *uint64 `json:"usage"`
				} `json:"usage"`
			} `json:"memory"`
		} `json:"data"`
	}
	if json.Unmarshal(out, &result) != nil || result.Type != "stats" || result.Data.CPU.Usage.Total == nil || result.Data.Memory.Usage.Usage == nil || *result.Data.Memory.Usage.Usage > math.MaxInt64 {
		return Metrics{}
	}
	return sampledMetrics(*result.Data.CPU.Usage.Total, int64(*result.Data.Memory.Usage.Usage), previous, now)
}

func sampledMetrics(total uint64, memory int64, previous Metrics, now time.Time) Metrics {
	m := Metrics{MemoryUsageBytes: memory, total: total, sample: now}
	if !previous.sample.IsZero() && m.total >= previous.total {
		elapsed := now.Sub(previous.sample).Nanoseconds()
		if elapsed > 0 {
			m.CPUUsageMillis = int64(float64(m.total-previous.total) / float64(elapsed) * 1000)
			m.Valid = true
		}
	}
	return m
}
