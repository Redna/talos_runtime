package main

import (
	"encoding/json"
	"fmt"
	"os"
	"time"
)

type Config struct {
	// Paths
	MemoryDir         string `json:"memory_dir"`
	SpineDir          string `json:"spine_dir"`
	ConstitutionPath  string `json:"constitution_path"`
	IdentityPath      string `json:"identity_path"`
	AppDir            string `json:"app_dir"`

	// Cortex
	CortexBin       string        `json:"cortex_bin"`
	CortexArgs      []string      `json:"cortex_args"`
	StartupTimeout  time.Duration `json:"startup_timeout"`

	// IPC
	SocketPath string `json:"socket_path"`

	// Control Plane
	ControlPlanePort int `json:"control_plane_port"`

	// Stream
	ContextThreshold float64 `json:"context_threshold"`
	ActiveWindow     int     `json:"active_window"` // M turns kept at full fidelity
	MaxContextTokens int     `json:"max_context_tokens"`

	// Gate
	GateURL string `json:"gate_url"`

	// Telegram
	TelegramBotToken string `json:"telegram_bot_token"`
	TelegramChatID   int64  `json:"telegram_chat_id"`

	// Health
	StallTimeout      time.Duration `json:"stall_timeout"`
	SnapshotInterval  int           `json:"snapshot_interval"` // every N turns
	MaxReversalDepth  int           `json:"max_reversal_depth"`

	// Shedding
	ShedToolOutputMaxChars int `json:"shed_tool_output_max_chars"`
}

func DefaultConfig() *Config {
	return &Config{
		MemoryDir:              "/memory",
		SpineDir:               "/spine",
		ConstitutionPath:        "/app/CONSTITUTION.md",
		IdentityPath:           "/app/identity.md",
		AppDir:                 "/app",
		CortexBin:              "/venv/bin/python",
		CortexArgs:             []string{"seed_agent.py"},
		StartupTimeout:         30 * time.Second,
		SocketPath:             "/tmp/spine.sock",
		ControlPlanePort:       4001,
		ContextThreshold:       0.85,
		ActiveWindow:           5,
		MaxContextTokens:       71680,
		GateURL:                "http://gate:4000",
		StallTimeout:           600 * time.Second,
		SnapshotInterval:       10,
		MaxReversalDepth:       5,
		ShedToolOutputMaxChars: 500,
	}
}

func LoadConfig(path string) (*Config, error) {
	cfg := DefaultConfig()
	data, err := os.ReadFile(path)
	if err != nil {
		if os.IsNotExist(err) {
			return cfg, nil
		}
		return nil, fmt.Errorf("read config: %w", err)
	}
	if err := json.Unmarshal(data, cfg); err != nil {
		return nil, fmt.Errorf("parse config: %w", err)
	}
	return cfg, nil
}