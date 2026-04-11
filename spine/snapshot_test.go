package main

import (
	"testing"
)

func TestSnapshotSaveAndLoad(t *testing.T) {
	dir := t.TempDir()
	sm := NewSnapshotManager(dir, 10)

	snapshot := &Snapshot{
		Focus:     "fix_auth_bug",
		TurnCount: 42,
	}

	if err := sm.Save(snapshot); err != nil {
		t.Fatalf("Save failed: %v", err)
	}

	loaded, err := sm.Load()
	if err != nil {
		t.Fatalf("Load failed: %v", err)
	}

	if loaded.Focus != "fix_auth_bug" {
		t.Errorf("Expected focus 'fix_auth_bug', got %q", loaded.Focus)
	}
	if loaded.TurnCount != 42 {
		t.Errorf("Expected turn count 42, got %d", loaded.TurnCount)
	}
}

func TestShouldSnapshot(t *testing.T) {
	sm := NewSnapshotManager("/tmp", 10)

	if sm.ShouldSnapshot(5) {
		t.Error("Should not snapshot at turn 5 with interval 10")
	}
	if !sm.ShouldSnapshot(10) {
		t.Error("Should snapshot at turn 10 with interval 10")
	}
	if !sm.ShouldSnapshot(20) {
		t.Error("Should snapshot at turn 20 with interval 10")
	}
}