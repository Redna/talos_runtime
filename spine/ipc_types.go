package main

// Cortex → Spine requests
type ThinkRequest struct {
	Focus   string    `json:"focus"`
	Tools   []ToolDef `json:"tools"`
	HUDData HUDData   `json:"hud_data"`
}

type ToolDef struct {
	Name        string                 `json:"name"`
	Description string                 `json:"description"`
	Parameters  map[string]interface{} `json:"parameters"` // JSON Schema object
}

type HUDData struct {
	MemoryKeys int      `json:"memory_keys"`
	LastKeys   []string `json:"last_keys"`
	Urgency    string   `json:"urgency"`
}

type ToolResultRequest struct {
	ToolCallID string `json:"tool_call_id"`
	Output     string `json:"output"`
	Success    bool   `json:"success"`
}

type RequestFoldRequest struct {
	Synthesis string `json:"synthesis"`
}

type RequestRestartRequest struct {
	Reason string `json:"reason"`
}

type SendMessageRequest struct {
	Channel string `json:"channel"`
	Text    string `json:"text"`
}

type EmitEventRequest struct {
	Type    string                 `json:"type"`
	Payload map[string]interface{} `json:"payload"`
}

type GetStateRequest struct {
	Keys []string `json:"keys"`
}

// Spine → Cortex push notifications
type PushNotification struct {
	Type    string `json:"type"` // "force_fold", "system_notice", "tool_timeout", "pause", "resume"
	Payload string `json:"payload"`
}

// Spine → Cortex think response
type ThinkResponse struct {
	AssistantMessage string          `json:"assistant_message"`
	ToolCalls        []ToolCallResult `json:"tool_calls"`
	ContextPct       float64          `json:"context_pct"`
	Turn             int             `json:"turn"`
	TokensUsed       int             `json:"tokens_used"`
	Folded           bool            `json:"folded"`
}

type ToolCallResult struct {
	ID        string                 `json:"id"`
	Name      string                 `json:"name"`
	Arguments map[string]interface{} `json:"arguments"`
}

// JSON-RPC wrapper
type JSONRPCRequest struct {
	JSONRPC string      `json:"jsonrpc"`
	ID      int         `json:"id"`
	Method  string      `json:"method"`
	Params  interface{} `json:"params"`
}

type JSONRPCResponse struct {
	JSONRPC string      `json:"jsonrpc"`
	ID      int         `json:"id"`
	Result  interface{} `json:"result,omitempty"`
	Error   *RPCError   `json:"error,omitempty"`
}

type RPCError struct {
	Code    int    `json:"code"`
	Message string `json:"message"`
}