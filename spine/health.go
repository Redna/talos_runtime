package main

import (
	"time"
)

type HealthMonitor struct {
	stallTimeout    time.Duration
	startupTimeout  time.Duration
	lastEventTime   time.Time
	cortexStartTime time.Time
	firstThinkDone  bool
}

func NewHealthMonitor(stallTimeout, startupTimeout time.Duration) *HealthMonitor {
	return &HealthMonitor{
		stallTimeout:   stallTimeout,
		startupTimeout: startupTimeout,
	}
}

func (h *HealthMonitor) RecordEvent() {
	h.lastEventTime = time.Now()
}

func (h *HealthMonitor) RecordFirstThink() {
	h.firstThinkDone = true
}

func (h *HealthMonitor) CortexStarted() {
	h.cortexStartTime = time.Now()
	h.firstThinkDone = false
	h.lastEventTime = time.Now()
}

func (h *HealthMonitor) IsStalled() bool {
	if h.lastEventTime.IsZero() {
		return true
	}
	return time.Since(h.lastEventTime) > h.stallTimeout
}

func (h *HealthMonitor) IsStartupFailure(exitCode int) bool {
	if h.firstThinkDone {
		return false // Cortex ran long enough
	}
	return time.Since(h.cortexStartTime) < h.startupTimeout
}
