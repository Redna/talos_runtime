package main

import (
	"testing"
)

func TestShedding(t *testing.T) {
	cfg := &Config{
		ActiveWindow:           2, // Small window to trigger shedding
		ShedToolOutputMaxChars: 100,
	}
	sm := NewStreamManager(cfg)

	// Create a stream with many turns so some fall outside the active window
	// Active window = 2 turns = last 4 messages
	// Messages 0-1 are frozen, messages 2-5 will be shed, messages 6-9 are in active window
	sm.messages = []Message{
		{Role: "system", Content: "System prompt"},                                     // 0 - frozen
		{Role: "user", Content: "Initialization"},                                       // 1 - frozen
		{Role: "assistant", Content: "Response 1", ToolCalls: []ToolCall{                // 2 - to shed
			{ID: "call1", Type: "function", Function: FunctionCall{Name: "read_file", Arguments: `{"path": "/some/long/path/file.txt"}`}},
		}},
		{Role: "tool", Content: "This is a very long tool output that exceeds the maximum character limit for shed messages and should be truncated with a suffix indicating archival", ToolCallID: "call1"}, // 3 - to shed
		{Role: "assistant", Content: "Response 2", ToolCalls: []ToolCall{                // 4 - to shed
			{ID: "call2", Type: "function", Function: FunctionCall{Name: "write_file", Arguments: `{"content": "some content"}`}},
		}},
		{Role: "tool", Content: "Short output", ToolCallID: "call2"},                    // 5 - to shed
		{Role: "user", Content: "User query 3 - recent"},                                // 6 - keep (in active window)
		{Role: "assistant", Content: "Response 3 - recent", ToolCalls: []ToolCall{       // 7 - keep (in active window)
			{ID: "call3", Type: "function", Function: FunctionCall{Name: "run_cmd", Arguments: `{"cmd": "ls"}`}},
		}},
		{Role: "user", Content: "User query 4 - recent"},                                // 8 - keep (in active window)
		{Role: "assistant", Content: "Response 4 - recent", ToolCalls: []ToolCall{       // 9 - keep (in active window)
			{ID: "call4", Type: "function", Function: FunctionCall{Name: "read_file", Arguments: `{"path": "/important/path"}`}},
		}},
	}

	// Apply shedding
	shedMessages := sm.applyShedding(sm.messages)

	// Verify frozen messages are unmodified
	if shedMessages[0].Content != "System prompt" {
		t.Errorf("Message 0 (system) was modified: %q", shedMessages[0].Content)
	}
	if shedMessages[1].Content != "Initialization" {
		t.Errorf("Message 1 (init) was modified: %q", shedMessages[1].Content)
	}

	// Verify shed assistant messages have stripped tool params (messages 2 and 4)
	if len(shedMessages[2].ToolCalls) > 0 {
		if shedMessages[2].ToolCalls[0].Function.Arguments != "{}" {
			t.Errorf("Tool arguments not stripped in shed message 2: %s", shedMessages[2].ToolCalls[0].Function.Arguments)
		}
	}
	if len(shedMessages[4].ToolCalls) > 0 {
		if shedMessages[4].ToolCalls[0].Function.Arguments != "{}" {
			t.Errorf("Tool arguments not stripped in shed message 4: %s", shedMessages[4].ToolCalls[0].Function.Arguments)
		}
	}

	// Verify shed tool messages have truncated output
	for _, msg := range shedMessages {
		if msg.Role == "tool" && msg.ToolCallID == "call1" {
			if len(msg.Content) > cfg.ShedToolOutputMaxChars+30 { // +30 for suffix
				t.Errorf("Tool output not truncated: %d chars", len(msg.Content))
			}
			if !contains(msg.Content, "... output archived ...") {
				t.Errorf("Tool output missing archival suffix: %q", msg.Content)
			}
		}
	}

	// Verify active window messages are unchanged (messages 6-9)
	if len(shedMessages[7].ToolCalls) > 0 {
		if shedMessages[7].ToolCalls[0].Function.Arguments != `{"cmd": "ls"}` {
			t.Errorf("Active window message 7 arguments incorrectly stripped: %s", shedMessages[7].ToolCalls[0].Function.Arguments)
		}
	}
	if len(shedMessages[9].ToolCalls) > 0 {
		if shedMessages[9].ToolCalls[0].Function.Arguments != `{"path": "/important/path"}` {
			t.Errorf("Active window message 9 arguments incorrectly stripped: %s", shedMessages[9].ToolCalls[0].Function.Arguments)
		}
	}
}

func TestFrozenPrefix(t *testing.T) {
	cfg := &Config{
		ActiveWindow:           2,
		ShedToolOutputMaxChars: 50,
	}
	sm := NewStreamManager(cfg)

	// Create messages with modifications that would normally be shed
	originalSystem := "Original system prompt - NEVER change"
	originalInit := "Original initialization - NEVER change"

	sm.messages = []Message{
		{Role: "system", Content: originalSystem},
		{Role: "user", Content: originalInit},
		{Role: "assistant", Content: "Response 1"},
		{Role: "tool", Content: "Output 1"},
	}

	// Apply shedding
	shedMessages := sm.applyShedding(sm.messages)

	// Verify messages 0 and 1 are NEVER modified
	if shedMessages[0].Content != originalSystem {
		t.Errorf("Frozen message 0 was modified!\nExpected: %q\nGot: %q", originalSystem, shedMessages[0].Content)
	}
	if shedMessages[1].Content != originalInit {
		t.Errorf("Frozen message 1 was modified!\nExpected: %q\nGot: %q", originalInit, shedMessages[1].Content)
	}
}

func TestFoldEnforcement(t *testing.T) {
	tools := []ToolDef{
		{Name: "read_file", Description: "Read a file"},
		{Name: "write_file", Description: "Write a file"},
		{Name: "run_command", Description: "Run a command"},
	}

	messages := []Message{
		{Role: "system", Content: "System"},
		{Role: "user", Content: "Init"},
		{Role: "assistant", Content: "Previous response"},
		{Role: "tool", Content: "Tool output"},
	}

	// Enforce fold
	foldedMessages, foldedTools := enforceFold(messages, tools)

	// Verify only fold_context tool is available
	if len(foldedTools) != 1 {
		t.Errorf("Expected 1 tool after fold, got %d", len(foldedTools))
	}
	if foldedTools[0].Name != "fold_context" {
		t.Errorf("Expected fold_context tool, got %s", foldedTools[0].Name)
	}

	// Verify frozen prefix is preserved
	if len(foldedMessages) < 2 {
		t.Fatalf("Expected at least 2 messages after fold, got %d", len(foldedMessages))
	}
	if foldedMessages[0].Content != "System" {
		t.Errorf("System prompt not preserved: %q", foldedMessages[0].Content)
	}
	if foldedMessages[1].Content != "Init" {
		t.Errorf("Init not preserved: %q", foldedMessages[1].Content)
	}
}

func TestHUDFormat(t *testing.T) {
	cfg := &Config{}
	sm := NewStreamManager(cfg)

	hudData := HUDData{
		MemoryKeys: 5,
		LastKeys:   []string{"fix_auth_bug"},
		Urgency:    "elevated",
	}

	hudStr := sm.formatHUD(hudData, 0.45, 1, 500, []string{"Crash detected"})

	// Verify HUD format components
	expectedComponents := []string{
		"[HUD",
		"Context: 45%",
		"Turn: 1",
		"Tokens: 500",
		"Memory: 5 keys",
		"Focus: fix_auth_bug",
		"[SYSTEM: Crash detected | Urgency: elevated]",
	}

	for _, component := range expectedComponents {
		if !contains(hudStr, component) {
			t.Errorf("HUD missing expected component %q\nFull HUD: %s", component, hudStr)
		}
	}
}

func TestFoldReplacesStream(t *testing.T) {
	cfg := &Config{}
	sm := NewStreamManager(cfg)

	synthesis := "This is the fold synthesis summarizing the conversation"

	sm.messages = []Message{
		{Role: "system", Content: "System prompt"},
		{Role: "user", Content: "Initialization"},
		{Role: "assistant", Content: "Response 1"},
		{Role: "tool", Content: "Tool output 1"},
		{Role: "assistant", Content: "Response 2"},
		{Role: "tool", Content: "Tool output 2"},
	}

	originalSystem := sm.messages[0].Content
	originalInit := sm.messages[1].Content

	// Apply fold
	sm.ApplyFold(synthesis)

	// Verify stream structure after fold
	if len(sm.messages) != 3 {
		t.Errorf("Expected 3 messages after fold, got %d", len(sm.messages))
	}

	// Verify frozen prefix unchanged
	if sm.messages[0].Content != originalSystem {
		t.Errorf("System prompt changed after fold: %q", sm.messages[0].Content)
	}
	if sm.messages[1].Content != originalInit {
		t.Errorf("Initialization changed after fold: %q", sm.messages[1].Content)
	}

	// Verify fold synthesis message
	if sm.messages[2].Role != "assistant" {
		t.Errorf("Expected assistant role for fold message, got %s", sm.messages[2].Role)
	}
	if sm.messages[2].Content != synthesis {
		t.Errorf("Fold synthesis not set correctly:\nExpected: %q\nGot: %q", synthesis, sm.messages[2].Content)
	}

	// Verify fold_context tool call
	if len(sm.messages[2].ToolCalls) != 1 {
		t.Errorf("Expected 1 tool call in fold message, got %d", len(sm.messages[2].ToolCalls))
	}
	if sm.messages[2].ToolCalls[0].Function.Name != "fold_context" {
		t.Errorf("Expected fold_context tool, got %s", sm.messages[2].ToolCalls[0].Function.Name)
	}
}

func TestRecordToolResult(t *testing.T) {
	cfg := &Config{}
	sm := NewStreamManager(cfg)

	sm.messages = []Message{
		{Role: "system", Content: "System"},
		{Role: "user", Content: "Init"},
	}

	result := ToolResultRequest{
		ToolCallID: "call_123",
		Output:     "Tool executed successfully",
		Success:    true,
	}

	sm.RecordToolResult(result)

	if len(sm.messages) != 3 {
		t.Errorf("Expected 3 messages after recording, got %d", len(sm.messages))
	}

	lastMsg := sm.messages[2]
	if lastMsg.Role != "tool" {
		t.Errorf("Expected tool role, got %s", lastMsg.Role)
	}
	if lastMsg.ToolCallID != "call_123" {
		t.Errorf("Expected tool_call_id 'call_123', got %s", lastMsg.ToolCallID)
	}
	if lastMsg.Content != "Tool executed successfully" {
		t.Errorf("Expected content 'Tool executed successfully', got %q", lastMsg.Content)
	}
}

func TestQueueSystemNotice(t *testing.T) {
	cfg := &Config{}
	sm := NewStreamManager(cfg)

	sm.QueueSystemNotice("Notice 1")
	sm.QueueSystemNotice("Notice 2")

	if len(sm.queuedNotices) != 2 {
		t.Errorf("Expected 2 queued notices, got %d", len(sm.queuedNotices))
	}
	if sm.queuedNotices[0] != "Notice 1" {
		t.Errorf("Expected 'Notice 1', got %q", sm.queuedNotices[0])
	}
	if sm.queuedNotices[1] != "Notice 2" {
		t.Errorf("Expected 'Notice 2', got %q", sm.queuedNotices[1])
	}
}

func TestGetState(t *testing.T) {
	cfg := &Config{}
	sm := NewStreamManager(cfg)

	sm.SetState("key1", "value1")
	sm.SetState("key2", 42)
	sm.SetState("key3", true)

	result := sm.GetState([]string{"key1", "key2", "nonexistent"})

	if result["key1"] != "value1" {
		t.Errorf("Expected key1=value1, got %v", result["key1"])
	}
	if result["key2"] != 42 {
		t.Errorf("Expected key2=42, got %v", result["key2"])
	}
	if _, exists := result["nonexistent"]; exists {
		t.Error("Expected nonexistent key to not exist")
	}
}

func TestSheddingNoOpWhenWithinWindow(t *testing.T) {
	cfg := &Config{
		ActiveWindow:           10,
		ShedToolOutputMaxChars: 50,
	}
	sm := NewStreamManager(cfg)

	// Create a small stream within the active window
	sm.messages = []Message{
		{Role: "system", Content: "System"},
		{Role: "user", Content: "Init"},
		{Role: "assistant", Content: "Response", ToolCalls: []ToolCall{
			{ID: "call1", Type: "function", Function: FunctionCall{Name: "test", Arguments: `{"data": "important"}`}},
		}},
	}

	originalArgs := sm.messages[2].ToolCalls[0].Function.Arguments

	shedMessages := sm.applyShedding(sm.messages)

	// Should be unchanged since within active window
	if len(shedMessages) != 3 {
		t.Errorf("Expected 3 messages, got %d", len(shedMessages))
	}
	if shedMessages[2].ToolCalls[0].Function.Arguments != originalArgs {
		t.Errorf("Arguments modified when they should not be: %s", shedMessages[2].ToolCalls[0].Function.Arguments)
	}
}

// Helper function
func contains(s, substr string) bool {
	return len(s) >= len(substr) && (s == substr || len(s) > len(substr) && findSubstring(s, substr))
}

func findSubstring(s, substr string) bool {
	for i := 0; i <= len(s)-len(substr); i++ {
		if s[i:i+len(substr)] == substr {
			return true
		}
	}
	return false
}
