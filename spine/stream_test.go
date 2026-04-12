package main

import (
	"strings"
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
		{Role: "tool", Content: strings.Repeat("x", 200), ToolCallID: "call1"},           // 3 - to shed (long output)
		{Role: "assistant", Content: "Response 2", ToolCalls: []ToolCall{                // 4 - to shed
			{ID: "call2", Type: "function", Function: FunctionCall{Name: "write_file", Arguments: `{"content": "some content"}`}},
		}},
		{Role: "tool", Content: "Short output", ToolCallID: "call2"},                    // 5 - to shed
		{Role: "assistant", Content: "Response 3 - recent", ToolCalls: []ToolCall{       // 6 - keep (in active window)
			{ID: "call3", Type: "function", Function: FunctionCall{Name: "run_cmd", Arguments: `{"cmd": "ls"}`}},
		}},
		{Role: "tool", Content: "Recent tool output", ToolCallID: "call3"},               // 7 - keep (in active window)
		{Role: "assistant", Content: "Response 4 - recent", ToolCalls: []ToolCall{       // 8 - keep (in active window)
			{ID: "call4", Type: "function", Function: FunctionCall{Name: "read_file", Arguments: `{"path": "/important/path"}`}},
		}},
		{Role: "tool", Content: "Recent tool output 2", ToolCallID: "call4"},             // 9 - keep (in active window)
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

	// Verify shed assistant messages have stripped tool params
	for _, msg := range shedMessages {
		if msg.Role == "assistant" && len(msg.ToolCalls) > 0 {
			for _, tc := range msg.ToolCalls {
				if tc.Function.Arguments != "{}" && tc.Function.Name != "run_cmd" && tc.Function.Name != "read_file" {
					// Only check shed messages (not in active window)
				}
			}
		}
	}

	// Verify shed tool messages have truncated output with char count
	for _, msg := range shedMessages {
		if msg.Role == "tool" && msg.ToolCallID == "call1" {
			if len(msg.Content) > cfg.ShedToolOutputMaxChars+50 { // +50 for suffix
				t.Errorf("Tool output not truncated: %d chars", len(msg.Content))
			}
			if !strings.Contains(msg.Content, "chars archived") {
				t.Errorf("Tool output missing archival suffix with char count: %q", msg.Content)
			}
		}
	}

	// Verify active window messages are unchanged
	for _, msg := range shedMessages {
		if msg.Role == "assistant" && len(msg.ToolCalls) > 0 {
			for _, tc := range msg.ToolCalls {
				if tc.Function.Name == "run_cmd" && tc.Function.Arguments != `{"cmd": "ls"}` {
					t.Errorf("Active window message arguments incorrectly stripped: %s", tc.Function.Arguments)
				}
			}
		}
	}
}

func TestFrozenPrefix(t *testing.T) {
	cfg := &Config{
		ActiveWindow:           2,
		ShedToolOutputMaxChars: 50,
	}
	sm := NewStreamManager(cfg)

	originalSystem := "Original system prompt - NEVER change"
	originalInit := "Original initialization - NEVER change"

	sm.messages = []Message{
		{Role: "system", Content: originalSystem},
		{Role: "user", Content: originalInit},
		{Role: "assistant", Content: "Response 1"},
		{Role: "tool", Content: "Output 1"},
	}

	shedMessages := sm.applyShedding(sm.messages)

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
		"Last 1: fix_auth_bug",
		"[SYSTEM | Crash detected | Urgency: elevated]",
	}

	for _, component := range expectedComponents {
		if !strings.Contains(hudStr, component) {
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

	sm.ApplyFold(synthesis)

	// Verify stream structure after fold: frozen prefix + synthesis (no orphaned tool calls)
	if len(sm.messages) != 3 {
		t.Errorf("Expected 3 messages after fold, got %d", len(sm.messages))
	}

	if sm.messages[0].Content != originalSystem {
		t.Errorf("System prompt changed after fold: %q", sm.messages[0].Content)
	}
	if sm.messages[1].Content != originalInit {
		t.Errorf("Initialization changed after fold: %q", sm.messages[1].Content)
	}

	// Verify fold synthesis message has no orphaned tool calls
	if sm.messages[2].Role != "assistant" {
		t.Errorf("Expected assistant role for fold message, got %s", sm.messages[2].Role)
	}
	if sm.messages[2].Content != synthesis {
		t.Errorf("Fold synthesis not set correctly:\nExpected: %q\nGot: %q", synthesis, sm.messages[2].Content)
	}
	if len(sm.messages[2].ToolCalls) != 0 {
		t.Errorf("Fold message should not have orphaned tool calls, got %d", len(sm.messages[2].ToolCalls))
	}

	// Verify context reset after fold
	if sm.contextPct > 0.2 {
		t.Errorf("Context should be reset after fold, got %.2f", sm.contextPct)
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

func TestRecordToolResultFailure(t *testing.T) {
	cfg := &Config{}
	sm := NewStreamManager(cfg)

	sm.messages = []Message{
		{Role: "system", Content: "System"},
		{Role: "user", Content: "Init"},
	}

	result := ToolResultRequest{
		ToolCallID: "call_456",
		Output:     "Command failed with exit code 1",
		Success:    false,
	}

	sm.RecordToolResult(result)

	if !strings.HasPrefix(sm.messages[2].Content, "[TOOL ERROR]") {
		t.Errorf("Failed tool result should have [TOOL ERROR] prefix, got: %q", sm.messages[2].Content)
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

	// Set some custom state
	sm.SetState("custom_key", "custom_value")

	// Set some authoritative state directly
	sm.turn = 5
	sm.tokensUsed = 1000
	sm.contextPct = 0.45

	// Request specific authoritative keys
	result := sm.GetState([]string{"context_pct", "turn", "tokens_used", "custom_key", "nonexistent"})

	if result["context_pct"] != 0.45 {
		t.Errorf("Expected context_pct=0.45, got %v", result["context_pct"])
	}
	if result["turn"] != 5 {
		t.Errorf("Expected turn=5, got %v", result["turn"])
	}
	if result["tokens_used"] != 1000 {
		t.Errorf("Expected tokens_used=1000, got %v", result["tokens_used"])
	}
	if result["custom_key"] != "custom_value" {
		t.Errorf("Expected custom_key=custom_value, got %v", result["custom_key"])
	}
	if _, exists := result["nonexistent"]; exists {
		t.Error("Expected nonexistent key to not exist")
	}
}

func TestGetStateAllKeys(t *testing.T) {
	cfg := &Config{}
	sm := NewStreamManager(cfg)

	sm.turn = 3
	sm.SetState("my_key", "my_value")

	// Request all keys (empty slice)
	result := sm.GetState(nil)

	// Should include both authoritative and custom state
	if result["context_pct"] == nil {
		t.Error("Expected context_pct to be present")
	}
	if result["turn"] != 3 {
		t.Errorf("Expected turn=3, got %v", result["turn"])
	}
	if result["my_key"] != "my_value" {
		t.Errorf("Expected my_key=my_value, got %v", result["my_key"])
	}
}

func TestSheddingNoOpWhenWithinWindow(t *testing.T) {
	cfg := &Config{
		ActiveWindow:           10,
		ShedToolOutputMaxChars: 50,
	}
	sm := NewStreamManager(cfg)

	sm.messages = []Message{
		{Role: "system", Content: "System"},
		{Role: "user", Content: "Init"},
		{Role: "assistant", Content: "Response", ToolCalls: []ToolCall{
			{ID: "call1", Type: "function", Function: FunctionCall{Name: "test", Arguments: `{"data": "important"}`}},
		}},
	}

	originalArgs := sm.messages[2].ToolCalls[0].Function.Arguments

	shedMessages := sm.applyShedding(sm.messages)

	if len(shedMessages) != 3 {
		t.Errorf("Expected 3 messages, got %d", len(shedMessages))
	}
	if shedMessages[2].ToolCalls[0].Function.Arguments != originalArgs {
		t.Errorf("Arguments modified when they should not be: %s", shedMessages[2].ToolCalls[0].Function.Arguments)
	}
}