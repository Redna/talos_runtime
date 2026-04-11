package main

type StreamManager struct {
	cfg *Config
}

func NewStreamManager(cfg *Config) *StreamManager {
	return &StreamManager{cfg: cfg}
}

func (sm *StreamManager) Think(req ThinkRequest) (*ThinkResponse, error) {
	// Will be implemented in Task 3
	return nil, nil
}

func (sm *StreamManager) RecordToolResult(result ToolResultRequest) {
	// Will be implemented in Task 3
}

func (sm *StreamManager) ApplyFold(synthesis string) {
	// Will be implemented in Task 3
}

func (sm *StreamManager) GetState(keys []string) map[string]interface{} {
	// Will be implemented in Task 3
	return nil
}

func (sm *StreamManager) QueueSystemNotice(notice string) {
	// Will be implemented in Task 3
}