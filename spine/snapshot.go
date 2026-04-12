package main

import (
	"encoding/json"
	"os"
	"path/filepath"
	"time"
)

type SnapshotManager struct {
	snapshotsDir   string
	interval       int // every N turns
	turnsSinceLast int
}

type Snapshot struct {
	Timestamp   string                   `json:"timestamp"`
	Focus       string                   `json:"focus"`
	MemoryState map[string]interface{}   `json:"memory_state"`
	TurnCount   int                      `json:"turn_count"`
	LastEvents  []map[string]interface{} `json:"last_events"`
}

func NewSnapshotManager(snapshotsDir string, interval int) *SnapshotManager {
	return &SnapshotManager{
		snapshotsDir:   snapshotsDir,
		interval:       interval,
		turnsSinceLast: 0,
	}
}

func (sm *SnapshotManager) ShouldSnapshot(turnCount int) bool {
	sm.turnsSinceLast = turnCount
	return turnCount%sm.interval == 0
}

func (sm *SnapshotManager) Save(snapshot *Snapshot) error {
	if err := os.MkdirAll(sm.snapshotsDir, 0755); err != nil {
		return err
	}
	snapshot.Timestamp = time.Now().UTC().Format(time.RFC3339)
	data, err := json.MarshalIndent(snapshot, "", "  ")
	if err != nil {
		return err
	}
	path := filepath.Join(sm.snapshotsDir, "last_good_state.json")
	return os.WriteFile(path, data, 0644)
}

func (sm *SnapshotManager) Load() (*Snapshot, error) {
	path := filepath.Join(sm.snapshotsDir, "last_good_state.json")
	data, err := os.ReadFile(path)
	if err != nil {
		return nil, err
	}
	var snapshot Snapshot
	if err := json.Unmarshal(data, &snapshot); err != nil {
		return nil, err
	}
	return &snapshot, nil
}