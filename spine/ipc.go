package main

type IPCServer struct {
	cfg           *Config
	supervisor    *Supervisor
	streamManager *StreamManager
	eventLogger   *EventLogger
}

func NewIPCServer(cfg *Config, supervisor *Supervisor, streamManager *StreamManager, eventLogger *EventLogger) *IPCServer {
	return &IPCServer{
		cfg:           cfg,
		supervisor:    supervisor,
		streamManager: streamManager,
		eventLogger:   eventLogger,
	}
}

func (ipc *IPCServer) Start() error {
	// Will be implemented in Task 2
	return nil
}

func (ipc *IPCServer) Stop() {
	// Will be implemented in Task 2
}