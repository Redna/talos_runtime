//go:build integration

package main

import (
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"os"
	"path/filepath"
	"testing"
	"time"
)

// TestControlPlaneIntegration tests the control plane with a real StreamManager
func TestControlPlaneIntegration(t *testing.T) {
	cfg := DefaultConfig()
	cfg.ControlPlanePort = 0 // let OS pick a port

	stream := NewStreamManager(cfg)
	events := NewEventLogger(t.TempDir())
	supervisor := NewSupervisor(cfg, events, nil, stream)

	cp := NewControlPlane(cfg, supervisor, stream, events)

	// Test /health endpoint
	req := httptest.NewRequest("GET", "/health", nil)
	w := httptest.NewRecorder()
	cp.handleHealth(w, req)

	if w.Code != http.StatusOK {
		t.Errorf("Expected 200, got %d", w.Code)
	}

	var result map[string]string
	json.NewDecoder(w.Body).Decode(&result)
	if result["status"] != "healthy" {
		t.Errorf("Expected healthy, got %q", result["status"])
	}

	// Test /status endpoint with real state
	stream.SetState("test_key", "test_value")
	req = httptest.NewRequest("GET", "/status", nil)
	w = httptest.NewRecorder()
	cp.handleStatus(w, req)

	if w.Code != http.StatusOK {
		t.Errorf("Expected 200, got %d", w.Code)
	}

	var statusResult map[string]interface{}
	json.NewDecoder(w.Body).Decode(&statusResult)
	if statusResult["test_key"] != "test_value" {
		t.Errorf("Expected test_key=test_value, got %v", statusResult["test_key"])
	}
}

// TestStreamManagerIntegration tests the full stream lifecycle
func TestStreamManagerIntegration(t *testing.T) {
	cfg := DefaultConfig()
	cfg.ActiveWindow = 3
	cfg.ShedToolOutputMaxChars = 50

	stream := NewStreamManager(cfg)

	// Simulate a series of tool calls and results
	stream.messages = []Message{
		{Role: "system", Content: "System prompt"},
		{Role: "user", Content: "Init"},
		{Role: "assistant", Content: "Thinking about the problem", ToolCalls: []ToolCall{
			{ID: "c1", Type: "function", Function: FunctionCall{Name: "read_file", Arguments: `{"path":"/test"}`}},
		}},
		{Role: "tool", Content: "File contents here", ToolCallID: "c1"},
		{Role: "assistant", Content: "I found the bug", ToolCalls: []ToolCall{
			{ID: "c2", Type: "function", Function: FunctionCall{Name: "write_file", Arguments: `{"path":"/fix","content":"fixed"}`}},
		}},
		{Role: "tool", Content: "Written successfully", ToolCallID: "c2"},
		{Role: "assistant", Content: "The fix is applied", ToolCalls: []ToolCall{
			{ID: "c3", Type: "function", Function: FunctionCall{Name: "bash_command", Arguments: `{"cmd":"pytest"}`}},
		}},
		{Role: "tool", Content: "All tests pass", ToolCallID: "c3"},
		{Role: "assistant", Content: "The fix is verified"},
	}

	// Test shedding
	shedMessages := stream.applyShedding(stream.messages)
	if len(shedMessages) < 2 {
		t.Errorf("Shedding should preserve at least system+init, got %d messages", len(shedMessages))
	}

	// Test fold
	stream.ApplyFold("Fixed the auth bug. Delta: changed validation logic. Negative: original approach was wrong. Handoff: tests pass.")
	if len(stream.messages) != 3 {
		t.Errorf("Expected 3 messages after fold, got %d", len(stream.messages))
	}

	// Test state after fold
	state := stream.GetState(nil)
	if state["context_pct"] == nil {
		t.Error("Expected context_pct in state after fold")
	}
}

// TestEventLoggerIntegration tests event emission and verifies the file was created
func TestEventLoggerIntegration(t *testing.T) {
	dir := t.TempDir()
	logger := NewEventLogger(dir)

	// Emit multiple events
	logger.Emit("test.event1", map[string]interface{}{"key": "value1"})
	logger.Emit("test.event2", map[string]interface{}{"key": "value2", "count": 42})
	logger.Close()

	// Verify events were written by checking the JSONL file exists
	pattern := filepath.Join(dir, "*.jsonl")
	matches, err := filepath.Glob(pattern)
	if err != nil {
		t.Fatalf("Failed to glob for event files: %v", err)
	}
	if len(matches) == 0 {
		t.Error("Expected at least one JSONL event file to be created")
	}

	// Verify file content is valid JSONL
	for _, match := range matches {
		data, err := os.ReadFile(match)
		if err != nil {
			t.Fatalf("Failed to read event file: %v", err)
		}
		lines := splitLines(string(data))
		if len(lines) < 2 {
			t.Errorf("Expected at least 2 event lines, got %d", len(lines))
		}
		// Verify each line is valid JSON
		for i, line := range lines {
			if line == "" {
				continue
			}
			var event map[string]interface{}
			if err := json.Unmarshal([]byte(line), &event); err != nil {
				t.Errorf("Line %d is not valid JSON: %v\nLine: %s", i, err, line)
			}
		}
	}
}

// TestSupervisorIntegration tests supervisor creation and health monitoring
func TestSupervisorIntegration(t *testing.T) {
	cfg := DefaultConfig()
	cfg.StallTimeout = 5 * time.Second
	cfg.StartupTimeout = 10 * time.Second

	events := NewEventLogger(t.TempDir())
	stream := NewStreamManager(cfg)
	supervisor := NewSupervisor(cfg, events, nil, stream)

	// Verify supervisor was created
	if supervisor == nil {
		t.Fatal("Expected non-nil supervisor")
	}

	// Verify health monitor was created
	if supervisor.health == nil {
		t.Error("Expected health monitor to be initialized")
	}

	// Verify initial state
	if supervisor.running != false {
		t.Error("Expected supervisor to not be running initially")
	}
}

// splitLines splits a string by newlines, filtering empty lines
func splitLines(s string) []string {
	var lines []string
	start := 0
	for i := 0; i < len(s); i++ {
		if s[i] == '\n' {
			line := s[start:i]
			if line != "" {
				lines = append(lines, line)
			}
			start = i + 1
		}
	}
	if start < len(s) {
		lines = append(lines, s[start:])
	}
	return lines
}