package main

import "net/http"

type ControlPlane struct {
	cfg            *Config
	supervisor     *Supervisor
	streamManager  *StreamManager
	eventLogger    *EventLogger
	server         *http.Server
}

func NewControlPlane(cfg *Config, supervisor *Supervisor, streamManager *StreamManager, eventLogger *EventLogger) *ControlPlane {
	return &ControlPlane{
		cfg:           cfg,
		supervisor:    supervisor,
		streamManager: streamManager,
		eventLogger:   eventLogger,
	}
}

func (cp *ControlPlane) Start() error {
	// Will be implemented in Task 6
	return nil
}

func (cp *ControlPlane) Stop() {
	// Will be implemented in Task 6
}
