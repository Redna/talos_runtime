package main

import (
	"os"
	"path/filepath"
	"strings"
	"testing"
)

func TestEventEmission(t *testing.T) {
	dir := t.TempDir()
	logger := NewEventLogger(dir)

	logger.Emit("cortex.think", map[string]interface{}{
		"turn":  42,
		"focus": "fix_auth_bug",
	})
	logger.Close()

	files, _ := filepath.Glob(filepath.Join(dir, "*.jsonl"))
	if len(files) != 1 {
		t.Fatalf("Expected 1 event file, got %d", len(files))
	}

	data, _ := os.ReadFile(files[0])
	if !strings.Contains(string(data), `"type":"cortex.think"`) {
		t.Errorf("Event file should contain cortex.think event, got: %s", string(data))
	}
	if !strings.Contains(string(data), `"focus":"fix_auth_bug"`) {
		t.Errorf("Event should contain focus field, got: %s", string(data))
	}
}