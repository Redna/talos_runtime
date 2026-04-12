package main

import (
	"bytes"
	"encoding/json"
	"fmt"
	"net/http"
	"strings"
	"sync"
)

// Message represents an LLM API message
type Message struct {
	Role       string     `json:"role"`
	Content    string     `json:"content,omitempty"`
	ToolCalls  []ToolCall `json:"tool_calls,omitempty"`
	ToolCallID string     `json:"tool_call_id,omitempty"`
	Name       string     `json:"name,omitempty"`
}

// ToolCall represents a tool call from the LLM
type ToolCall struct {
	ID       string                 `json:"id"`
	Type     string                 `json:"type"`
	Function FunctionCall           `json:"function"`
	Arguments map[string]interface{} `json:"-"` // parsed from Function.Arguments
}

// FunctionCall represents a function call within a tool call
type FunctionCall struct {
	Name      string `json:"name"`
	Arguments string `json:"arguments"` // JSON string
}

// StreamManager owns the message stream and handles LLM interactions
type StreamManager struct {
	cfg             *Config
	mu              sync.RWMutex
	messages        []Message
	turn            int
	tokensUsed      int
	contextPct      float64
	queuedNotices   []string
	state           map[string]interface{}
	constitutionMgr *ConstitutionManager
}

// NewStreamManager creates a new StreamManager
func NewStreamManager(cfg *Config) *StreamManager {
	constitutionMgr := NewConstitutionManager(cfg.ConstitutionPath, cfg.IdentityPath)

	return &StreamManager{
		cfg:             cfg,
		constitutionMgr: constitutionMgr,
		messages:        make([]Message, 0),
		turn:            0,
		tokensUsed:      0,
		contextPct:      0.0,
		queuedNotices:   make([]string, 0),
		state:           make(map[string]interface{}),
	}
}

// Think is the main entry point. Constructs the full LLM API payload,
// sends it to the Gate, and parses the response.
func (sm *StreamManager) Think(req ThinkRequest) (*ThinkResponse, error) {
	sm.mu.Lock()
	defer sm.mu.Unlock()

	// Reload constitution only if hash has changed (Frozen Stream Invariant)
	if changed, err := sm.constitutionMgr.ReloadIfChanged(); err != nil {
		return nil, fmt.Errorf("load constitution: %w", err)
	} else if changed {
		sm.state["constitution_reloaded"] = true
	}

	// Build the payload (system prompt + stream + HUD piggybacked)
	messages := sm.buildPayload(req)

	// Build the API request
	apiReq := GateRequest{
		Model:    "talos",
		Messages: messages,
		Tools:    req.Tools,
	}

	// Check if we need to enforce a fold
	if sm.contextPct > sm.cfg.ContextThreshold {
		apiReq.Messages, apiReq.Tools = enforceFold(apiReq.Messages, req.Tools)
		apiReq.ToolChoice = map[string]string{"type": "function", "name": "fold_context"}
		// Inject fold urgency notice
		sm.queuedNotices = append(sm.queuedNotices,
			fmt.Sprintf("Context at %.0f%%. You MUST use fold_context immediately.", sm.contextPct*100))
	}

	// Send to Gate
	resp, err := sm.sendToGate(apiReq)
	if err != nil {
		return nil, fmt.Errorf("gate call: %w", err)
	}

	// Parse the response
	var assistantContent string
	var toolCalls []ToolCall
	if len(resp.Choices) > 0 {
		assistantContent = resp.Choices[0].Message.Content
		toolCalls = resp.Choices[0].Message.ToolCalls
	}

	thinkResp := &ThinkResponse{
		AssistantMessage: assistantContent,
		ContextPct:       resp.Usage.ContextPct,
		Turn:             sm.turn,
		TokensUsed:       resp.Usage.TotalTokens,
		Folded:           false,
	}

	// Extract tool calls
	for _, tc := range toolCalls {
		thinkResp.ToolCalls = append(thinkResp.ToolCalls, ToolCallResult{
			ID:        tc.ID,
			Name:      tc.Function.Name,
			Arguments: parseArguments(tc.Function.Arguments),
		})
	}

	// Record the assistant message in the stream
	sm.messages = append(sm.messages, Message{
		Role:      "assistant",
		Content:   assistantContent,
		ToolCalls: convertToolCalls(toolCalls),
	})

	// Update state
	sm.turn++
	sm.tokensUsed = resp.Usage.TotalTokens
	sm.contextPct = resp.Usage.ContextPct

	return thinkResp, nil
}

// GateRequest is the request to the Gate API
type GateRequest struct {
	Model      string                 `json:"model"`
	Messages   []Message              `json:"messages"`
	Tools      []ToolDef              `json:"tools,omitempty"`
	ToolChoice interface{}            `json:"tool_choice,omitempty"`
}

// GateResponse is the response from the Gate API
type GateResponse struct {
	Choices []struct {
		Message struct {
			Role      string     `json:"role"`
			Content   string     `json:"content"`
			ToolCalls []ToolCall  `json:"tool_calls,omitempty"`
		} `json:"message"`
	} `json:"choices"`
	Usage struct {
		PromptTokens     int     `json:"prompt_tokens"`
		CompletionTokens int     `json:"completion_tokens"`
		TotalTokens      int     `json:"total_tokens"`
		ContextPct       float64 `json:"context_pct"`
	} `json:"usage"`
}

func (sm *StreamManager) sendToGate(req GateRequest) (*GateResponse, error) {
	body, err := json.Marshal(req)
	if err != nil {
		return nil, fmt.Errorf("marshal request: %w", err)
	}

	httpReq, err := http.NewRequest("POST", sm.cfg.GateURL+"/v1/chat/completions", bytes.NewReader(body))
	if err != nil {
		return nil, fmt.Errorf("create request: %w", err)
	}
	httpReq.Header.Set("Content-Type", "application/json")

	client := &http.Client{}
	resp, err := client.Do(httpReq)
	if err != nil {
		return nil, fmt.Errorf("http call: %w", err)
	}
	defer resp.Body.Close()

	if resp.StatusCode != http.StatusOK {
		return nil, fmt.Errorf("gate returned status %d", resp.StatusCode)
	}

	var gateResp GateResponse
	if err := json.NewDecoder(resp.Body).Decode(&gateResp); err != nil {
		return nil, fmt.Errorf("decode response: %w", err)
	}

	return &gateResp, nil
}

// buildPayload constructs the message array for the LLM API call.
// Applies shedding to the current stream, then prepends system prompt.
// The HUD is piggybacked onto the last message content, never as a separate message.
func (sm *StreamManager) buildPayload(req ThinkRequest) []Message {
	// Start with system prompt
	systemMsg := Message{
		Role:    "system",
		Content: sm.constitutionMgr.SystemPrompt(),
	}

	// Apply shedding to current messages
	shedMessages := sm.applyShedding(sm.messages)

	// Build HUD string
	hudStr := sm.formatHUD(
		req.HUDData,
		sm.contextPct,
		sm.turn,
		sm.tokensUsed,
		sm.queuedNotices,
	)
	sm.queuedNotices = nil // clear queued notices after formatting

	// Add focus as user message
	focusMsg := Message{
		Role:    "user",
		Content: req.Focus,
	}

	// Construct payload: system + shed stream + focus
	messages := []Message{systemMsg}
	messages = append(messages, shedMessages...)
	messages = append(messages, focusMsg)

	// Piggyback HUD onto the last message content (never as a separate message)
	if len(messages) > 0 && hudStr != "" {
		messages[len(messages)-1].Content += "\n" + hudStr
	}

	return messages
}

// applyShedding applies fixed-window shedding to messages.
// Messages 0 and 1 (system prompt and initialization) are never modified (Frozen Stream Invariant).
// For messages beyond the active window (last M turns):
// - Keep assistant message content but strip tool parameters
// - Truncate tool outputs to ShedToolOutputMaxChars characters
func (sm *StreamManager) applyShedding(messages []Message) []Message {
	if len(messages) <= 2 {
		return messages // nothing to shed
	}

	// Messages 0 and 1 are frozen (system + init)
	frozenCount := 2
	activeWindow := sm.cfg.ActiveWindow

	// Count messages from the end to determine which are in the active window.
	// We keep the last `activeWindow * 2` messages at full fidelity as a heuristic
	// (each "turn" typically has an assistant + tool_result pair).
	// Messages beyond that boundary are shed.
	activeMessageCount := activeWindow * 2

	if len(messages) <= frozenCount+activeMessageCount {
		return messages // all within window
	}

	result := make([]Message, 0, len(messages))

	// Keep frozen prefix
	for i := 0; i < frozenCount; i++ {
		result = append(result, messages[i])
	}

	// Shed messages between frozen prefix and active window
	shedBoundary := len(messages) - activeMessageCount
	for i := frozenCount; i < shedBoundary; i++ {
		result = append(result, sm.shedMessage(messages[i]))
	}

	// Keep active window at full fidelity
	for i := shedBoundary; i < len(messages); i++ {
		result = append(result, messages[i])
	}

	return result
}

// shedMessage sheds a single message according to the rules
func (sm *StreamManager) shedMessage(msg Message) Message {
	switch msg.Role {
	case "assistant":
		// Keep content (reasoning is never shed)
		// Strip tool parameters (keep only tool names)
		if len(msg.ToolCalls) > 0 {
			shedCalls := make([]ToolCall, len(msg.ToolCalls))
			for i, tc := range msg.ToolCalls {
				shedCalls[i] = ToolCall{
					ID:   tc.ID,
					Type: tc.Type,
					Function: FunctionCall{
						Name:      tc.Function.Name,
						Arguments: "{}", // strip arguments
					},
				}
			}
			msg.ToolCalls = shedCalls
		}
	case "tool":
		// Truncate tool output
		if len(msg.Content) > sm.cfg.ShedToolOutputMaxChars {
			maxChars := sm.cfg.ShedToolOutputMaxChars
			truncated := msg.Content[:maxChars]
			archivedChars := len(msg.Content) - maxChars
			msg.Content = fmt.Sprintf("%s\n[… %d chars archived]", truncated, archivedChars)
		}
	}
	return msg
}

// formatHUD formats the HUD string
func (sm *StreamManager) formatHUD(hudData HUDData, contextPct float64, turn int, tokensUsed int, queuedNotices []string) string {
	var parts []string

	// Main HUD section
	hudParts := []string{
		"[HUD",
		fmt.Sprintf("Context: %.0f%%", contextPct*100),
		fmt.Sprintf("Turn: %d", turn),
		fmt.Sprintf("Tokens: %d", tokensUsed),
		fmt.Sprintf("Memory: %d keys", hudData.MemoryKeys),
	}

	if len(hudData.LastKeys) > 0 {
		hudParts = append(hudParts, fmt.Sprintf("Last %d: %s", len(hudData.LastKeys), strings.Join(hudData.LastKeys, ", ")))
	}

	mainHUD := strings.Join(hudParts, " | ")
	parts = append(parts, mainHUD+"]")

	// System notices
	if len(queuedNotices) > 0 {
		for _, notice := range queuedNotices {
			parts = append(parts, fmt.Sprintf("[SYSTEM | %s | Urgency: %s]", notice, hudData.Urgency))
		}
	}

	return strings.Join(parts, " ")
}

// enforceFold enforces fold when context exceeds threshold.
// Returns only the frozen prefix + last assistant message with fold_context as the only available tool.
func enforceFold(messages []Message, tools []ToolDef) ([]Message, []ToolDef) {
	if len(messages) < 2 {
		return messages, tools
	}

	// Keep only frozen prefix (messages 0 and 1)
	foldedMessages := make([]Message, 0, 3)
	foldedMessages = append(foldedMessages, messages[0]) // system prompt
	if len(messages) > 1 {
		foldedMessages = append(foldedMessages, messages[1]) // initialization
	}

	// Find last assistant message for context continuity
	for i := len(messages) - 1; i >= 0; i-- {
		if messages[i].Role == "assistant" {
			foldedMessages = append(foldedMessages, messages[i])
			break
		}
	}

	// Override tools to only fold_context
	foldTool := ToolDef{
		Name:        "fold_context",
		Description: "Compress the conversation context into a summary",
		Parameters: map[string]interface{}{
			"type": "object",
			"properties": map[string]interface{}{
				"synthesis": map[string]interface{}{
					"type":        "string",
					"description": "A concise summary of the conversation using the DELTA pattern: State Delta, Negative Knowledge, Handoff",
				},
			},
			"required": []string{"synthesis"},
		},
	}

	return foldedMessages, []ToolDef{foldTool}
}

// RecordToolResult records a tool result in the stream
func (sm *StreamManager) RecordToolResult(result ToolResultRequest) {
	sm.mu.Lock()
	defer sm.mu.Unlock()

	content := result.Output
	if !result.Success {
		content = "[TOOL ERROR] " + content
	}

	msg := Message{
		Role:       "tool",
		Content:    content,
		ToolCallID: result.ToolCallID,
	}

	sm.messages = append(sm.messages, msg)
}

// ApplyFold replaces the stream with frozen prefix + fold synthesis message.
// Clears all other messages.
func (sm *StreamManager) ApplyFold(synthesis string) {
	sm.mu.Lock()
	defer sm.mu.Unlock()

	if len(sm.messages) < 2 {
		// Not enough messages to fold
		return
	}

	// Keep only frozen prefix + synthesis as assistant message (no orphaned tool calls)
	sm.messages = []Message{
		sm.messages[0], // system prompt
		sm.messages[1], // initialization
		{
			Role:    "assistant",
			Content: synthesis,
		},
	}

	sm.turn++
	sm.contextPct = 0.1 // Reset context after fold
}

// GetState returns authoritative state values
func (sm *StreamManager) GetState(keys []string) map[string]interface{} {
	sm.mu.RLock()
	defer sm.mu.RUnlock()

	// Always include authoritative state values
	authoritative := map[string]interface{}{
		"context_pct":     sm.contextPct,
		"turn":            sm.turn,
		"tokens_used":     sm.tokensUsed,
		"message_count":   len(sm.messages),
		"queued_notices":  len(sm.queuedNotices),
	}

	// If specific keys requested, filter
	if len(keys) > 0 {
		result := make(map[string]interface{})
		for _, key := range keys {
			if val, ok := authoritative[key]; ok {
				result[key] = val
			} else if val, ok := sm.state[key]; ok {
				result[key] = val
			}
		}
		return result
	}

	// Return all authoritative + custom state
	result := make(map[string]interface{})
	for k, v := range authoritative {
		result[k] = v
	}
	for k, v := range sm.state {
		result[k] = v
	}
	return result
}

// QueueSystemNotice queues a system notice to be injected in the next HUD piggyback
func (sm *StreamManager) QueueSystemNotice(notice string) {
	sm.mu.Lock()
	defer sm.mu.Unlock()

	sm.queuedNotices = append(sm.queuedNotices, notice)
}

// SetState sets a state value
func (sm *StreamManager) SetState(key string, value interface{}) {
	sm.mu.Lock()
	defer sm.mu.Unlock()

	sm.state[key] = value
}

// GetMessages returns a copy of the current messages (for testing/debugging)
func (sm *StreamManager) GetMessages() []Message {
	sm.mu.RLock()
	defer sm.mu.RUnlock()

	result := make([]Message, len(sm.messages))
	copy(result, sm.messages)
	return result
}

// parseArguments parses a JSON string into a map
func parseArguments(args string) map[string]interface{} {
	var result map[string]interface{}
	if err := json.Unmarshal([]byte(args), &result); err != nil {
		return make(map[string]interface{})
	}
	return result
}

// convertToolCalls converts Gate tool calls to internal format
func convertToolCalls(calls []ToolCall) []ToolCall {
	result := make([]ToolCall, len(calls))
	for i, tc := range calls {
		result[i] = ToolCall{
			ID:   tc.ID,
			Type: tc.Type,
			Function: FunctionCall{
				Name:      tc.Function.Name,
				Arguments: tc.Function.Arguments,
			},
			Arguments: parseArguments(tc.Function.Arguments),
		}
	}
	return result
}