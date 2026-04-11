package main

type StreamManager struct {
	cfg *Config
}

func NewStreamManager(cfg *Config) *StreamManager {
	return &StreamManager{cfg: cfg}
}
