package main

import "log"

type Supervisor struct {
	cfg              *Config
	eventLogger      *EventLogger
	snapshotManager  *SnapshotManager
	streamManager    *StreamManager
	stopCh           chan struct{}
}

func NewSupervisor(cfg *Config, eventLogger *EventLogger, snapshotManager *SnapshotManager, streamManager *StreamManager) *Supervisor {
	return &Supervisor{
		cfg:             cfg,
		eventLogger:     eventLogger,
		snapshotManager: snapshotManager,
		streamManager:   streamManager,
		stopCh:          make(chan struct{}),
	}
}

func (s *Supervisor) Run() {
	log.Println("[Supervisor] Waiting for stop signal")
	<-s.stopCh
	log.Println("[Supervisor] Received stop signal")
}

func (s *Supervisor) Stop() {
	close(s.stopCh)
}

func (s *Supervisor) RequestRestart(reason string) {
	// Will be implemented in Task 5
}
